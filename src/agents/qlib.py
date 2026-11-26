"""Qlib 训练产物加载与线上/回测确定性推理 Agent。

加载由 Qlib 研究流水线训练并经独立时间窗口校准（Platt 概率校准 + Conformal 残差下界）
导出的标准产物目录，向 ModelPredictor 输出统一口径的扣费净收益预测。
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar, Literal

import lightgbm as lgb
import numpy as np
from scipy.optimize import minimize
from scipy.special import expit

from src.contracts.domains.feature import FeatureSet, ModelPrediction
from src.contracts.enums.trading import HoldingPeriod


class QlibAgent:
    """加载 Qlib 标准化校准产物并执行严格 PIT 净收益推理的 Agent。"""

    _VERSION: ClassVar[str] = "qlib_v1"
    _FEATURE_NAMES: ClassVar[list[str]] = [
        name for name in FeatureSet.model_fields if name not in {"quote_id", "as_of"}
    ]

    def __init__(self, model_dir: str | Path) -> None:
        manifest_path = Path(model_dir) / "manifest.json"
        if not manifest_path.exists():
            raise ValueError(f"未找到 Qlib 模型清单文件：{manifest_path}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("version") != self._VERSION:
            raise ValueError("Qlib 模型产物版本不匹配")
        if not manifest.get("serving_compatible", False):
            raise ValueError("该 Qlib 产物为离线研究模型（serving_compatible=false），禁止直接接入线上推理")
        features = manifest.get("features")
        if not isinstance(features, list) or not features or any(name not in self._FEATURE_NAMES for name in features):
            raise ValueError("Qlib 模型特征列表无效或与 FeatureSet 不兼容")
        model_type = manifest.get("model_type")
        if model_type not in {"lgb", "linear"}:
            raise ValueError(f"不支持的 Qlib 模型类型：{model_type}")

        self._model_dir = Path(model_dir)
        self._model_type: Literal["lgb", "linear"] = model_type
        self._features: list[str] = [str(name) for name in features]
        self._experiment_name: str = str(manifest.get("experiment_name", "unknown"))
        self._run_id: str = str(manifest.get("run_id", "unknown"))
        self._source_type: str = str(manifest.get("source_type", "local_sqlite"))
        self._factor_preset: str = str(manifest.get("factor_preset", "gold_core"))
        self._lgb_models: dict[HoldingPeriod, tuple[lgb.Booster, lgb.Booster, dict[str, Any]]] = {}
        self._linear_models: dict[HoldingPeriod, dict[str, Any]] = {}

        horizons = manifest.get("horizons")
        if not isinstance(horizons, dict) or not horizons:
            raise ValueError("Qlib 模型产物缺少可用持有期")

        for horizon_key, entry in horizons.items():
            self._validate_horizon_entry(entry)
            horizon = HoldingPeriod(horizon_key)
            if self._model_type == "lgb":
                regressor = lgb.Booster(model_str=entry["regressor"])
                classifier = lgb.Booster(model_str=entry["classifier"])
                if regressor.feature_name() != self._features or classifier.feature_name() != self._features:
                    raise ValueError("Qlib LightGBM 权重特征顺序与清单不一致")
                self._lgb_models[horizon] = (regressor, classifier, entry)
            else:
                weights = entry.get("weights")
                means = entry.get("feature_means")
                stds = entry.get("feature_stds")
                intercept = entry.get("intercept", 0.0)
                if (
                    not isinstance(weights, list)
                    or not isinstance(means, list)
                    or not isinstance(stds, list)
                    or len(weights) != len(self._features)
                    or len(means) != len(self._features)
                    or len(stds) != len(self._features)
                ):
                    raise ValueError("Qlib 线性模型权重或标准化参数维度不匹配")
                try:
                    w_vals = [float(v) for v in weights]
                    m_vals = [float(v) for v in means]
                    s_vals = [float(v) for v in stds]
                    b_val = float(intercept)
                except (TypeError, ValueError) as exc:
                    raise ValueError("Qlib 线性模型参数必须为数值") from exc
                if not all(math.isfinite(v) for v in (*w_vals, *m_vals, *s_vals, b_val)) or any(v < 0 for v in s_vals):
                    raise ValueError("Qlib 线性模型参数包含非有限数或负标准差")
                self._linear_models[horizon] = entry

    @staticmethod
    def _validate_horizon_entry(entry: Any) -> None:
        if not isinstance(entry, dict):
            raise ValueError("Qlib 持有期条目格式无效")
        for key in ("probability_slope", "probability_intercept", "residual_bound", "confidence_level"):
            if key not in entry:
                raise ValueError(f"Qlib 校准产物缺少必要字段：{key}")
            try:
                val = float(entry[key])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Qlib 校准字段 {key} 必须为数值") from exc
            if not math.isfinite(val):
                raise ValueError("Qlib 校准产物包含非有限数")
        if (
            float(entry["probability_slope"]) < 0
            or float(entry["residual_bound"]) < 0
            or not 0.5 < float(entry["confidence_level"]) < 1.0
        ):
            raise ValueError("Qlib 校准参数范围无效")
        if "trained_until" not in entry or "calibrated_until" not in entry:
            raise ValueError("Qlib 产物缺少 trained_until 或 calibrated_until 时间边界")
        try:
            trained_until = datetime.fromisoformat(str(entry["trained_until"]))
            calibrated_until = datetime.fromisoformat(str(entry["calibrated_until"]))
        except ValueError as exc:
            raise ValueError("Qlib 产物时间边界格式无效") from exc
        if (
            trained_until.utcoffset() is None
            or calibrated_until.utcoffset() is None
            or trained_until >= calibrated_until
        ):
            raise ValueError("Qlib 产物时间边界无效")

    def _extract_vector(self, features: FeatureSet) -> np.ndarray:
        values: list[float] = []
        for name in self._features:
            value = getattr(features, name)
            if value is None:
                values.append(float("nan"))
            elif not math.isfinite(float(value)):
                raise ValueError(f"特征 {name} 必须为有限数或缺失值")
            else:
                values.append(float(value))
        return np.array([values], dtype=np.float64)

    @staticmethod
    def _fit_probability(scores: np.ndarray, labels: np.ndarray) -> tuple[float, float]:
        """使用 L2 正则化逻辑回归拟合 Platt 概率校准斜率与截距。"""

        def objective(parameters: np.ndarray) -> tuple[float, np.ndarray]:
            logits = parameters[0] * scores + parameters[1]
            errors = expit(logits) - labels
            loss = float(np.mean(np.logaddexp(0, logits) - labels * logits) + 1e-4 * parameters[0] ** 2)
            gradient = np.array(
                [float(np.mean(errors * scores) + 2e-4 * parameters[0]), float(np.mean(errors))],
                dtype=np.float64,
            )
            return loss, gradient

        result = minimize(
            objective,
            np.array([1.0, 0.0], dtype=np.float64),
            jac=True,
            method="L-BFGS-B",
            bounds=[(0.0, 100.0), (-30.0, 30.0)],
        )
        if not result.success:
            raise ValueError("Qlib 盈利概率校准未收敛")
        return float(result.x[0]), float(result.x[1])

    @staticmethod
    def _compute_conformal_bound(predicted: np.ndarray, actual: np.ndarray, confidence_level: float) -> float:
        """按单侧 Conformal 残差分位数计算置信下界偏移量。"""
        residuals = np.maximum(predicted - actual, 0.0)
        rank = math.ceil((len(actual) + 1) * confidence_level)
        if rank > len(actual):
            raise ValueError("校准样本不足以支持指定置信水平")
        return float(np.sort(residuals)[rank - 1])

    def _predict_linear(self, vector: np.ndarray, entry: dict[str, Any]) -> tuple[float, float, dict[str, float]]:
        means = np.asarray(entry["feature_means"], dtype=np.float64)
        stds = np.asarray(entry["feature_stds"], dtype=np.float64)
        safe_stds = np.where(stds > 1e-12, stds, 1.0)
        filled = np.where(np.isnan(vector[0]), means, vector[0])
        standardized = (filled - means) / safe_stds
        weights = np.asarray(entry["weights"], dtype=np.float64)
        expected = float(np.dot(standardized, weights) + float(entry.get("intercept", 0.0)))
        abs_weights = np.abs(weights)
        total = float(np.sum(abs_weights))
        importance = {
            name: float(w) / total if total > 0 else 0.0 for name, w in zip(self._features, abs_weights, strict=True)
        }
        return expected, expected, importance

    @classmethod
    def fit_calibration_entry(
        cls,
        *,
        cal_predicted: np.ndarray,
        cal_raw_scores: np.ndarray,
        cal_actual: np.ndarray,
        confidence_level: float,
        training_count: int,
        purged_count: int,
        trained_until: datetime,
        calibrated_until: datetime,
        extra_payload: dict[str, Any],
    ) -> dict[str, Any]:
        """根据独立校准窗口预测结果拟合 Platt 概率校准与 Conformal 残差下界。"""
        if not 0.5 < confidence_level < 1.0:
            raise ValueError("置信水平必须介于 0.5 与 1.0 之间")
        if len(cal_actual) < 20:
            raise ValueError("校准样本不足 20 条，无法生成可靠置信下界")
        labels = (cal_actual > 0).astype(np.float64)
        if len(set(labels.tolist())) < 2:
            raise ValueError("校准集必须同时包含盈利与亏损样本")
        slope, intercept = cls._fit_probability(cal_raw_scores, labels)
        residual_bound = cls._compute_conformal_bound(cal_predicted, cal_actual, confidence_level)
        calibrated_prob = expit(slope * cal_raw_scores + intercept)
        brier = float(np.mean((calibrated_prob - labels) ** 2))
        return {
            **extra_payload,
            "probability_slope": slope,
            "probability_intercept": intercept,
            "residual_bound": residual_bound,
            "confidence_level": confidence_level,
            "training_count": training_count,
            "calibration_count": len(cal_actual),
            "purged_count": purged_count,
            "trained_until": trained_until.isoformat(),
            "calibrated_until": calibrated_until.isoformat(),
            "calibration_brier": brier,
        }

    @classmethod
    def save_artifact(
        cls,
        output_dir: str | Path,
        *,
        model_type: Literal["lgb", "linear"],
        features: list[str],
        horizons: dict[str, dict[str, Any]],
        experiment_name: str,
        run_id: str,
        source_type: str,
        factor_preset: str,
        serving_compatible: bool,
    ) -> Path:
        """将 Qlib 训练与校准产物写入新目录（拒绝覆盖已有产物）。"""
        directory = Path(output_dir)
        directory.mkdir(parents=True, exist_ok=True)
        manifest = {
            "version": cls._VERSION,
            "model_type": model_type,
            "features": features,
            "experiment_name": experiment_name,
            "run_id": run_id,
            "source_type": source_type,
            "factor_preset": factor_preset,
            "serving_compatible": serving_compatible,
            "production_approved": False,
            "horizons": horizons,
        }
        target = directory / "manifest.json"
        try:
            with target.open("x", encoding="utf-8") as stream:
                stream.write(json.dumps(manifest, ensure_ascii=False, allow_nan=False, indent=2))
        except FileExistsError as exc:
            raise ValueError("输出目录已有 Qlib 模型产物，请使用新的版本目录") from exc
        return directory

    def predict(self, features: FeatureSet, horizon: HoldingPeriod) -> ModelPrediction:
        """基于 Qlib 校准产物输出目标持有期的扣费净收益预测。"""
        if self._model_type == "lgb":
            if horizon not in self._lgb_models:
                raise ValueError(f"没有 {horizon.value} 期限的 Qlib 模型")
            regressor, classifier, metadata = self._lgb_models[horizon]
        else:
            if horizon not in self._linear_models:
                raise ValueError(f"没有 {horizon.value} 期限的 Qlib 模型")
            metadata = self._linear_models[horizon]
            regressor = None
            classifier = None

        calibrated_until = datetime.fromisoformat(str(metadata["calibrated_until"]))
        if features.as_of.utcoffset() is None or features.as_of <= calibrated_until:
            raise ValueError("推理时间必须晚于 Qlib 模型校准标签结束时间")

        vector = self._extract_vector(features)
        if self._model_type == "lgb" and regressor is not None and classifier is not None:
            expected = float(np.asarray(regressor.predict(vector, num_threads=2))[0])
            raw_score = float(np.asarray(classifier.predict(vector, raw_score=True, num_threads=2))[0])
            raw_imp = regressor.feature_importance(importance_type="gain")
            total = float(np.sum(raw_imp))
            importance = {
                name: float(val) / total if total > 0 else 0.0
                for name, val in zip(self._features, raw_imp, strict=True)
            }
        else:
            expected, raw_score, importance = self._predict_linear(vector, metadata)

        probability = float(
            expit(float(metadata["probability_slope"]) * raw_score + float(metadata["probability_intercept"]))
        )
        bound = float(metadata["residual_bound"])
        if not all(math.isfinite(val) for val in (expected, probability, bound)):
            raise ValueError("Qlib 模型输出包含非有限数")

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

    def get_model_info(self) -> dict[str, Any]:
        """返回 Qlib 产物版本、实验来源及各持有期校准摘要。"""
        hidden = {"regressor", "classifier", "weights", "feature_means", "feature_stds"}
        horizons_info: dict[str, Any] = {}
        if self._model_type == "lgb":
            for horizon, (_, _, entry) in self._lgb_models.items():
                horizons_info[horizon.value] = {k: v for k, v in entry.items() if k not in hidden}
        else:
            for horizon, entry in self._linear_models.items():
                horizons_info[horizon.value] = {k: v for k, v in entry.items() if k not in hidden}
        return {
            "model_version": self._VERSION,
            "model_type": self._model_type,
            "experiment_name": self._experiment_name,
            "run_id": self._run_id,
            "source_type": self._source_type,
            "factor_preset": self._factor_preset,
            "device": "cpu",
            "production_approved": False,
            "horizons": horizons_info,
        }
