"""TimesFM 多因子融合模型产物加载与线上/回测确定性推理 Agent。

产物由 TimesFM 多因子训练流水线导出：TimesFM 条件预测的收益分布特征与截面因子拼接后，
由 LightGBM 或岭回归融合头输出扣费净收益，并经独立时间窗口校准（Platt 概率校准 + Conformal 残差下界）。
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Literal

import lightgbm as lgb
import numpy as np
from scipy.special import expit

from src.agents.qlib import QlibAgent
from src.contracts.domains.feature import FeatureSet, ModelPrediction
from src.contracts.domains.timesfm_multifactor import TimesFMDistributionProvider
from src.contracts.enums.trading import HoldingPeriod


class TimesFMMultiFactorAgent:
    """加载 TimesFM 多因子融合产物并执行严格 PIT 净收益推理的 Agent。"""

    _VERSION: ClassVar[str] = "timesfm_multifactor_v1"
    _HIDDEN_KEYS: ClassVar[frozenset[str]] = frozenset(
        {"regressor", "classifier", "weights", "feature_means", "feature_stds"}
    )
    MANIFEST_NAME: ClassVar[str] = "manifest.json"
    DIST_FEATURE_NAMES: ClassVar[tuple[str, ...]] = (
        "timesfm_forecast_return",
        "timesfm_q10_return",
        "timesfm_q90_return",
        "timesfm_iqr_return",
        "timesfm_path_mean_return",
        "timesfm_path_min_return",
        "timesfm_skew",
    )

    def __init__(self, model_dir: str | Path, *, timesfm_agent: TimesFMDistributionProvider | None = None) -> None:
        directory = Path(model_dir)
        manifest, checkpoint_dir = self._load_manifest(directory)
        head_type = manifest.get("head_model_type")
        if head_type not in {"lgb", "ridge"}:
            raise ValueError(f"不支持的融合头类型：{head_type}")
        features = manifest.get("features")
        if not isinstance(features, list) or not features or len(set(features)) != len(features):
            raise ValueError("TimesFM 多因子模型特征列表无效")
        horizons = manifest["horizons"]

        self._model_dir = directory
        self._head_type: Literal["lgb", "ridge"] = head_type
        self._features: list[str] = [str(name) for name in features]
        self._manifest: dict[str, Any] = manifest
        self._timesfm = timesfm_agent
        self._lgb_models: dict[HoldingPeriod, tuple[lgb.Booster, lgb.Booster, dict[str, Any]]] = {}
        self._ridge_models: dict[HoldingPeriod, dict[str, Any]] = {}
        self._checkpoint_dir: Path | None = checkpoint_dir

        for horizon_key, entry in horizons.items():
            self._validate_entry(entry)
            horizon = HoldingPeriod(horizon_key)
            if self._head_type == "lgb":
                reg_str = entry.get("regressor")
                clf_str = entry.get("classifier")
                if not isinstance(reg_str, str) or not reg_str or not isinstance(clf_str, str) or not clf_str:
                    raise ValueError("TimesFM LightGBM 融合头缺少有效模型文本")
                try:
                    regressor = lgb.Booster(model_str=reg_str)
                    classifier = lgb.Booster(model_str=clf_str)
                except Exception as exc:
                    raise ValueError(f"TimesFM LightGBM 融合头加载失败：{exc}") from exc
                if regressor.feature_name() != self._features or classifier.feature_name() != self._features:
                    raise ValueError("TimesFM LightGBM 融合头特征顺序与清单不一致")
                self._lgb_models[horizon] = (regressor, classifier, entry)
            else:
                self._validate_ridge(entry)
                self._ridge_models[horizon] = entry

    @classmethod
    def _load_manifest(cls, directory: Path) -> tuple[dict[str, Any], Path | None]:
        """读取并校验模型清单基础结构、版本、上线兼容性与微调权重目录边界。"""
        manifest_path = directory / cls.MANIFEST_NAME
        if not manifest_path.is_file():
            raise ValueError(f"未找到 TimesFM 多因子模型清单文件：{manifest_path}")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("TimesFM 多因子模型清单 JSON 格式无效") from exc
        if not isinstance(manifest, dict):
            raise ValueError("TimesFM 多因子模型清单根节点必须为 JSON 对象")
        if manifest.get("version") != cls._VERSION:
            raise ValueError("TimesFM 多因子模型产物版本不匹配")
        if not manifest.get("serving_compatible", False):
            raise ValueError("该 TimesFM 产物 serving_compatible=false，禁止直接接入线上推理")
        horizons = manifest.get("horizons")
        if not isinstance(horizons, dict) or not horizons:
            raise ValueError("TimesFM 多因子模型产物缺少可用持有期")
        checkpoint_dir: Path | None = None
        relpath = manifest.get("finetuned_checkpoint_relpath")
        if relpath:
            if not isinstance(relpath, str):
                raise ValueError("微调权重相对路径格式无效")
            candidate = (directory / relpath).resolve()
            if not candidate.is_relative_to(directory.resolve()) or not candidate.is_dir():
                raise ValueError("微调权重目录无效或越出模型目录")
            checkpoint_dir = candidate
        return manifest, checkpoint_dir

    @staticmethod
    def _validate_entry(entry: Any) -> None:
        if not isinstance(entry, dict):
            raise ValueError("TimesFM 持有期条目格式无效")
        for key in ("probability_slope", "probability_intercept", "residual_bound", "confidence_level"):
            if key not in entry:
                raise ValueError(f"TimesFM 校准产物缺少必要字段：{key}")
            try:
                value = float(entry[key])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"TimesFM 校准字段 {key} 必须为数值") from exc
            if not math.isfinite(value):
                raise ValueError("TimesFM 校准产物包含非有限数")
        if (
            float(entry["probability_slope"]) < 0
            or float(entry["residual_bound"]) < 0
            or not 0.5 < float(entry["confidence_level"]) < 1.0
        ):
            raise ValueError("TimesFM 校准参数范围无效")
        try:
            trained_until = datetime.fromisoformat(str(entry["trained_until"]))
            calibrated_until = datetime.fromisoformat(str(entry["calibrated_until"]))
        except (KeyError, ValueError) as exc:
            raise ValueError("TimesFM 产物缺少或包含无效的时间边界") from exc
        if (
            trained_until.utcoffset() is None
            or calibrated_until.utcoffset() is None
            or trained_until >= calibrated_until
        ):
            raise ValueError("TimesFM 产物时间边界无效")

    def _validate_ridge(self, entry: dict[str, Any]) -> None:
        size = len(self._features)
        for key in ("weights", "feature_means", "feature_stds"):
            values = entry.get(key)
            if not isinstance(values, list) or len(values) != size:
                raise ValueError("TimesFM 岭回归权重或标准化参数维度不匹配")
            try:
                if not all(math.isfinite(float(item)) for item in values):
                    raise ValueError("TimesFM 岭回归参数包含非有限数")
            except (TypeError, ValueError) as exc:
                raise ValueError("TimesFM 岭回归参数必须为有限数值") from exc
        if any(float(item) < 0 for item in entry["feature_stds"]) or not math.isfinite(
            float(entry.get("intercept", 0.0))
        ):
            raise ValueError("TimesFM 岭回归参数包含负标准差或非有限截距")

    def _vector(self, factors: dict[str, float | None]) -> np.ndarray:
        values: list[float] = []
        for name in self._features:
            value = factors.get(name)
            if value is None:
                values.append(float("nan"))
            elif not math.isfinite(float(value)):
                raise ValueError(f"特征 {name} 必须为有限数或缺失值")
            else:
                values.append(float(value))
        return np.array([values], dtype=np.float64)

    @classmethod
    def read_backbone_config(cls, model_dir: str | Path) -> dict[str, Any]:
        """受校验地读取产物中的主干权重目录、上下文长度与点预测线性校准参数。"""
        manifest, checkpoint_dir = cls._load_manifest(Path(model_dir))
        horizons = manifest["horizons"]
        raw_entry = horizons.get("1w") if isinstance(horizons.get("1w"), dict) else next(iter(horizons.values()), None)
        if not isinstance(raw_entry, dict):
            raise ValueError("TimesFM 持有期条目格式无效")
        raw_context = manifest.get("context_length")
        context_length: int | None = None
        if raw_context is not None:
            try:
                context_length = int(raw_context)
            except (TypeError, ValueError) as exc:
                raise ValueError("TimesFM 产物 context_length 必须为整数") from exc
        try:
            slope = float(raw_entry.get("timesfm_calibration_slope", 1.0))
            intercept = float(raw_entry.get("timesfm_calibration_intercept", 0.0))
        except (TypeError, ValueError) as exc:
            raise ValueError("TimesFM 产物线性校准参数必须为数值") from exc
        if not math.isfinite(slope) or not math.isfinite(intercept):
            raise ValueError("TimesFM 产物线性校准参数必须为有限数值")
        return {
            "checkpoint_path": str(checkpoint_dir) if checkpoint_dir is not None else None,
            "context_length": context_length,
            "calibration_slope": slope,
            "calibration_intercept": intercept,
        }

    @classmethod
    def fit_calibration_entry(
        cls,
        *,
        cal_predicted: np.ndarray,
        cal_raw_scores: np.ndarray,
        cal_actual: np.ndarray,
        cal_timesfm_point: np.ndarray,
        confidence_level: float,
        training_count: int,
        purged_count: int,
        trained_until: datetime,
        calibrated_until: datetime,
        extra_payload: dict[str, Any],
    ) -> dict[str, Any]:
        """拟合 Platt 概率校准、Conformal 残差下界，并附带 TimesFM 点预测线性校准参数。"""
        slope, intercept = 1.0, 0.0
        finite_mask = np.isfinite(cal_timesfm_point) & np.isfinite(cal_actual)
        valid_point = cal_timesfm_point[finite_mask]
        valid_actual = cal_actual[finite_mask]
        if len(valid_point) >= 2:
            variance = float(np.var(valid_point))
            if math.isfinite(variance) and variance > 1e-18:
                covariance = float(np.mean((valid_point - valid_point.mean()) * (valid_actual - valid_actual.mean())))
                cand_slope = float(np.clip(covariance / variance, 0.0, 10.0))
                cand_intercept = float(valid_actual.mean() - cand_slope * valid_point.mean())
                if math.isfinite(cand_slope) and math.isfinite(cand_intercept):
                    slope, intercept = cand_slope, cand_intercept
        return QlibAgent.fit_calibration_entry(
            cal_predicted=cal_predicted,
            cal_raw_scores=cal_raw_scores,
            cal_actual=cal_actual,
            confidence_level=confidence_level,
            training_count=training_count,
            purged_count=purged_count,
            trained_until=trained_until,
            calibrated_until=calibrated_until,
            extra_payload={
                **extra_payload,
                "timesfm_calibration_slope": slope,
                "timesfm_calibration_intercept": intercept,
            },
        )

    @classmethod
    def save_artifact(
        cls,
        output_dir: str | Path,
        *,
        head_model_type: Literal["lgb", "ridge"],
        features: list[str],
        horizons: dict[str, dict[str, Any]],
        metadata: dict[str, Any],
        finetuned_checkpoint_relpath: str | None = None,
    ) -> Path:
        """将融合头与校准产物写入新目录（拒绝覆盖已有产物）。"""
        directory = Path(output_dir)
        directory.mkdir(parents=True, exist_ok=True)
        manifest = {
            **metadata,
            "version": cls._VERSION,
            "head_model_type": head_model_type,
            "features": features,
            "serving_compatible": True,
            "production_approved": False,
            "finetuned_checkpoint_relpath": finetuned_checkpoint_relpath,
            "horizons": horizons,
        }
        target = directory / cls.MANIFEST_NAME
        try:
            with target.open("x", encoding="utf-8") as stream:
                stream.write(json.dumps(manifest, ensure_ascii=False, allow_nan=False, indent=2))
        except FileExistsError as exc:
            raise ValueError("输出目录已有 TimesFM 多因子模型产物，请使用新的版本目录") from exc
        return directory

    @property
    def model_dir(self) -> Path:
        """返回产物目录。"""
        return self._model_dir

    @property
    def horizons(self) -> list[HoldingPeriod]:
        """返回产物已训练的持有期列表。"""
        source = self._lgb_models if self._head_type == "lgb" else self._ridge_models
        return list(source.keys())

    @property
    def calibrated_until(self) -> datetime:
        """返回所有持有期中最晚的校准标签结束时刻（样本外起点下界）。"""
        entries = (
            [item[2] for item in self._lgb_models.values()]
            if self._head_type == "lgb"
            else list(self._ridge_models.values())
        )
        return max(datetime.fromisoformat(str(entry["calibrated_until"])) for entry in entries).astimezone(UTC)

    @property
    def manifest(self) -> dict[str, Any]:
        """返回产物清单（包含校准参数，不含大权重对象请勿直接对外暴露）。"""
        return self._manifest

    @property
    def checkpoint_dir(self) -> Path | None:
        """返回已解析的微调权重目录，未微调时为 None。"""
        return self._checkpoint_dir

    def predict_from_factors(
        self,
        factor_values: dict[str, float | None],
        horizon: HoldingPeriod,
        *,
        as_of: datetime,
    ) -> ModelPrediction:
        """按特征清单组装向量，校验 PIT 时间门禁后输出扣费净收益预测。"""
        if self._head_type == "lgb":
            if horizon not in self._lgb_models:
                raise ValueError(f"没有 {horizon.value} 期限的 TimesFM 多因子模型")
            regressor, classifier, metadata = self._lgb_models[horizon]
        else:
            if horizon not in self._ridge_models:
                raise ValueError(f"没有 {horizon.value} 期限的 TimesFM 多因子模型")
            metadata = self._ridge_models[horizon]
            regressor = classifier = None

        calibrated_until = datetime.fromisoformat(str(metadata["calibrated_until"]))
        if as_of.utcoffset() is None or as_of <= calibrated_until:
            raise ValueError("推理时间必须晚于 TimesFM 多因子模型校准标签结束时间")

        vector = self._vector(factor_values)
        if regressor is not None and classifier is not None:
            expected = float(np.asarray(regressor.predict(vector, num_threads=2))[0])
            raw_score = float(np.asarray(classifier.predict(vector, raw_score=True, num_threads=2))[0])
            raw_imp = regressor.feature_importance(importance_type="gain")
            total = float(np.sum(raw_imp))
            importance = {
                name: float(val) / total if total > 0 else 0.0
                for name, val in zip(self._features, raw_imp, strict=True)
            }
        else:
            means = np.asarray(metadata["feature_means"], dtype=np.float64)
            stds = np.asarray(metadata["feature_stds"], dtype=np.float64)
            safe_stds = np.where(stds > 1e-12, stds, 1.0)
            filled = np.where(np.isnan(vector[0]), means, vector[0])
            weights = np.asarray(metadata["weights"], dtype=np.float64)
            expected = float(np.dot((filled - means) / safe_stds, weights) + float(metadata.get("intercept", 0.0)))
            raw_score = expected
            abs_weights = np.abs(weights)
            total = float(np.sum(abs_weights))
            importance = {
                name: float(val) / total if total > 0 else 0.0
                for name, val in zip(self._features, abs_weights, strict=True)
            }

        probability = float(
            expit(float(metadata["probability_slope"]) * raw_score + float(metadata["probability_intercept"]))
        )
        bound = float(metadata["residual_bound"])
        if not all(math.isfinite(val) for val in (expected, probability, bound)):
            raise ValueError("TimesFM 多因子模型输出包含非有限数")
        return ModelPrediction(
            horizon=horizon,
            expected_net_return=expected,
            net_return_lcb=expected - bound,
            win_probability=probability,
            uncertainty=bound,
            feature_importance=importance,
            model_version=self._VERSION,
            is_calibrated=True,
        )

    def predict(self, features: FeatureSet, horizon: HoldingPeriod) -> ModelPrediction:
        """实现净收益模型协议：由 FeatureSet 与已缓存的 TimesFM 分布特征融合预测。"""
        factors: dict[str, float | None] = {}
        for name in FeatureSet.model_fields:
            if name in {"quote_id", "as_of"}:
                continue
            value = getattr(features, name)
            factors[name] = None if value is None else float(value)
        dist = self._timesfm.get_cached_distribution(features.as_of, horizon) if self._timesfm is not None else None
        if dist is not None:
            factors["timesfm_forecast_return"] = dist.point_return
            factors["timesfm_q10_return"] = dist.q10_return
            factors["timesfm_q90_return"] = dist.q90_return
            factors["timesfm_iqr_return"] = dist.iqr_return
            factors["timesfm_path_mean_return"] = dist.path_mean_return
            factors["timesfm_path_min_return"] = dist.path_min_return
            factors["timesfm_skew"] = dist.skew
        return self.predict_from_factors(factors, horizon, as_of=features.as_of)

    def get_model_info(self) -> dict[str, Any]:
        """返回产物版本、训练模式、融合头与各持有期校准摘要（隐藏权重与树模型文本）。"""
        source = (
            {h: item[2] for h, item in self._lgb_models.items()} if self._head_type == "lgb" else self._ridge_models
        )
        horizons_info = {
            horizon.value: {k: v for k, v in entry.items() if k not in self._HIDDEN_KEYS}
            for horizon, entry in source.items()
        }
        return {
            "model_version": self._VERSION,
            "head_model_type": self._head_type,
            "train_mode": self._manifest.get("train_mode"),
            "experiment_name": self._manifest.get("experiment_name"),
            "run_id": self._manifest.get("run_id"),
            "factor_preset": self._manifest.get("factor_preset"),
            "use_covariates": self._manifest.get("use_covariates"),
            "context_length": self._manifest.get("context_length"),
            "freq": self._manifest.get("freq"),
            "finetuned_checkpoint": str(self._checkpoint_dir) if self._checkpoint_dir else None,
            "device": "cpu",
            "production_approved": False,
            "horizons": horizons_info,
        }
