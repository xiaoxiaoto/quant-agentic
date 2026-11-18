"""真实 LightGBM 预测 Agent，按期限训练、时间校准与推理。"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

import lightgbm as lgb
import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator
from scipy.optimize import minimize
from scipy.special import expit

from src.contracts.domains.feature import FeatureSet, ModelPrediction
from src.contracts.enums.trading import HoldingPeriod


class TrainingSample(BaseModel):
    """标签必须是已扣除双边费用与点差的实际净收益。"""

    _horizon_hours: ClassVar[dict[HoldingPeriod, int]] = {
        HoldingPeriod.H1: 1,
        HoldingPeriod.H4: 4,
        HoldingPeriod.D1: 24,
        HoldingPeriod.D3: 72,
        HoldingPeriod.W1: 168,
        HoldingPeriod.W2: 336,
        HoldingPeriod.M1: 720,
    }
    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")
    features: FeatureSet
    horizon: HoldingPeriod
    net_return: float = Field(gt=-1.0)
    label_end_at: datetime

    @model_validator(mode="after")
    def validate_times(self) -> TrainingSample:
        """拒绝无时区或尚未覆盖持有期的标签。"""
        start = self.features.as_of
        if start.utcoffset() is None or self.label_end_at.utcoffset() is None:
            raise ValueError("训练时间必须携带时区")
        if self.label_end_at <= start:
            raise ValueError("标签结束时间必须晚于特征时间")
        if (self.label_end_at - start).total_seconds() < self._horizon_hours[self.horizon] * 3600:
            raise ValueError("标签结束时间未覆盖目标持有期")
        return self


class LightGBMAgent:
    """按期限加载训练产物并输出量化收益预测的 Agent；校准不代表已获生产准入。"""

    _feature_names = [name for name in FeatureSet.model_fields if name not in {"quote_id", "as_of"}]

    def __init__(self, model_dir: str | Path) -> None:
        manifest = json.loads((Path(model_dir) / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("version") != "lightgbm_v1" or manifest.get("features") != self._feature_names:
            raise ValueError("LightGBM 产物版本或特征顺序不匹配")
        self._models: dict[HoldingPeriod, tuple[lgb.Booster, lgb.Booster, dict[str, Any]]] = {}
        for horizon, entry in manifest["horizons"].items():
            for key in ("probability_slope", "probability_intercept", "residual_bound", "confidence_level"):
                if not math.isfinite(float(entry[key])):
                    raise ValueError("LightGBM 校准产物包含非有限数")
            if entry["probability_slope"] < 0 or entry["residual_bound"] < 0 or not 0.5 < entry["confidence_level"] < 1:
                raise ValueError("LightGBM 校准参数无效")
            trained_until = datetime.fromisoformat(entry["trained_until"])
            calibrated_until = datetime.fromisoformat(entry["calibrated_until"])
            if (
                trained_until.utcoffset() is None
                or calibrated_until.utcoffset() is None
                or trained_until >= calibrated_until
            ):
                raise ValueError("LightGBM 产物时间边界无效")
            self._models[HoldingPeriod(horizon)] = (
                lgb.Booster(model_str=entry["regressor"]),
                lgb.Booster(model_str=entry["classifier"]),
                entry,
            )
            if any(
                booster.feature_name() != self._feature_names for booster in self._models[HoldingPeriod(horizon)][:2]
            ):
                raise ValueError("LightGBM 权重特征顺序不匹配")
        if not self._models:
            raise ValueError("LightGBM 产物没有可用期限")

    @classmethod
    def _vector(cls, features: FeatureSet) -> list[float]:
        values = []
        for name in cls._feature_names:
            value = getattr(features, name)
            if value is None:
                values.append(float("nan"))
            elif not math.isfinite(float(value)):
                raise ValueError(f"特征 {name} 必须为有限数或缺失值")
            else:
                values.append(float(value))
        return values

    @staticmethod
    def _fit_probability(scores: Any, labels: Any) -> tuple[float, float]:
        def objective(parameters: Any) -> tuple[float, Any]:
            logits = parameters[0] * scores + parameters[1]
            errors = expit(logits) - labels
            loss = np.mean(np.logaddexp(0, logits) - labels * logits) + 1e-4 * parameters[0] ** 2
            gradient = np.array([np.mean(errors * scores) + 2e-4 * parameters[0], np.mean(errors)])
            return float(loss), gradient

        result = minimize(objective, np.array([1.0, 0.0]), jac=True, method="L-BFGS-B", bounds=[(0, 100), (-30, 30)])
        if not result.success:
            raise ValueError("盈利概率校准未收敛")
        return float(result.x[0]), float(result.x[1])

    @classmethod
    def train(
        cls,
        samples: list[TrainingSample],
        output_dir: str | Path,
        confidence_level: float = 0.9,
        num_boost_round: int = 80,
    ) -> LightGBMAgent:
        """按时间保留后 25% 校准，并剔除跨越分割点的训练标签。"""
        if not 0.5 < confidence_level < 1 or num_boost_round < 1:
            raise ValueError("置信水平或训练轮数无效")
        grouped: dict[HoldingPeriod, list[TrainingSample]] = {}
        for sample in samples:
            sample = TrainingSample.model_validate(sample.model_dump())
            cls._vector(sample.features)
            rows = grouped.setdefault(sample.horizon, [])
            if rows and sample.features.as_of <= rows[-1].features.as_of:
                raise ValueError("每个期限的训练样本必须严格按时间递增")
            rows.append(sample)
        if not grouped:
            raise ValueError("训练样本不能为空")
        entries: dict[str, Any] = {}
        for horizon, rows in grouped.items():
            split = int(len(rows) * 0.75)
            calibration = rows[split:]
            if len(calibration) < 20:
                raise ValueError("每个期限至少需要 80 条样本，其中 20 条用于校准")
            calibration_start = calibration[0].features.as_of
            training = [row for row in rows[:split] if row.label_end_at < calibration_start]
            if len(training) < 40:
                raise ValueError("剔除跨分割标签后训练样本不足 40 条")
            train_x = np.array([cls._vector(row.features) for row in training])
            train_y = np.array([row.net_return for row in training])
            cal_x = np.array([cls._vector(row.features) for row in calibration])
            cal_y = np.array([row.net_return for row in calibration])
            if len(set(train_y > 0)) < 2 or len(set(cal_y > 0)) < 2:
                raise ValueError("训练集与校准集必须各自包含盈利及亏损样本")
            parameters = {
                "verbosity": -1,
                "num_threads": 2,
                "seed": 42,
                "deterministic": True,
                "force_col_wise": True,
                "num_leaves": 7,
                "min_data_in_leaf": 5,
                "learning_rate": 0.05,
            }
            regressor = lgb.train(
                {**parameters, "objective": "regression"},
                lgb.Dataset(train_x, label=train_y, feature_name=cls._feature_names),
                num_boost_round=num_boost_round,
            )
            classifier = lgb.train(
                {**parameters, "objective": "binary"},
                lgb.Dataset(train_x, label=(train_y > 0).astype(int), feature_name=cls._feature_names),
                num_boost_round=num_boost_round,
            )
            raw_scores = classifier.predict(cal_x, raw_score=True, num_threads=2)
            slope, intercept = cls._fit_probability(raw_scores, (cal_y > 0).astype(int))
            residuals = np.maximum(np.asarray(regressor.predict(cal_x, num_threads=2)) - cal_y, 0)
            rank = math.ceil((len(calibration) + 1) * confidence_level)
            if rank > len(calibration):
                raise ValueError("校准样本不足以支持指定置信水平")
            residual_bound = float(np.sort(residuals)[rank - 1])
            calibrated_probability = expit(slope * np.asarray(raw_scores) + intercept)
            entries[horizon.value] = {
                "regressor": regressor.model_to_string(),
                "classifier": classifier.model_to_string(),
                "probability_slope": slope,
                "probability_intercept": intercept,
                "residual_bound": residual_bound,
                "confidence_level": confidence_level,
                "training_count": len(training),
                "calibration_count": len(calibration),
                "purged_count": split - len(training),
                "trained_until": max(row.label_end_at for row in training).isoformat(),
                "calibrated_until": max(row.label_end_at for row in calibration).isoformat(),
                "calibration_brier": float(np.mean((calibrated_probability - (cal_y > 0)) ** 2)),
            }
        directory = Path(output_dir)
        directory.mkdir(parents=True, exist_ok=True)
        manifest = {
            "version": "lightgbm_v1",
            "features": cls._feature_names,
            "production_approved": False,
            "horizons": entries,
        }
        target = directory / "manifest.json"
        try:
            with target.open("x", encoding="utf-8") as stream:
                stream.write(json.dumps(manifest, ensure_ascii=False, allow_nan=False))
        except FileExistsError as exc:
            raise ValueError("输出目录已有模型，请使用新的版本目录") from exc
        return cls(directory)

    def predict(self, features: FeatureSet, horizon: HoldingPeriod) -> ModelPrediction:
        """使用净收益标签模型，避免重复扣费；拒绝产物形成前的回放。"""
        if horizon not in self._models:
            raise ValueError(f"没有 {horizon.value} 期限的 LightGBM 模型")
        regressor, classifier, metadata = self._models[horizon]
        if features.as_of.utcoffset() is None or features.as_of <= datetime.fromisoformat(metadata["calibrated_until"]):
            raise ValueError("推理时间必须晚于模型校准标签结束时间")
        vector = np.array([self._vector(features)])
        expected = float(np.asarray(regressor.predict(vector, num_threads=2))[0])
        raw_score = float(np.asarray(classifier.predict(vector, raw_score=True, num_threads=2))[0])
        probability = float(expit(metadata["probability_slope"] * raw_score + metadata["probability_intercept"]))
        bound = float(metadata["residual_bound"])
        importance = regressor.feature_importance(importance_type="gain")
        total = float(np.sum(importance))
        if not all(math.isfinite(value) for value in (expected, probability, bound)):
            raise ValueError("LightGBM 输出含非有限数")
        return ModelPrediction(
            horizon=horizon,
            expected_net_return=expected,
            net_return_lcb=expected - bound,
            win_probability=probability,
            uncertainty=bound,
            feature_importance={
                name: float(value) / total if total else 0.0 for name, value in zip(self._feature_names, importance)
            },
            model_version="lightgbm_v1",
            is_calibrated=True,
        )

    def get_model_info(self) -> dict[str, Any]:
        """报告训练和校准元数据，不声称已获生产批准。"""
        return {
            "model_version": "lightgbm_v1",
            "device": "cpu",
            "production_approved": False,
            "horizons": {
                horizon.value: {key: value for key, value in entry.items() if key not in {"regressor", "classifier"}}
                for horizon, (_, _, entry) in self._models.items()
            },
        }

    @classmethod
    def main(cls) -> None:
        """从已完成点时特征与净收益标注的 JSONL 训练。"""
        parser = argparse.ArgumentParser(description="训练并时间校准 LightGBM 净收益模型")
        parser.add_argument("--input", required=True)
        parser.add_argument("--output", required=True)
        parser.add_argument("--confidence", type=float, default=0.9)
        arguments = parser.parse_args()
        with Path(arguments.input).open(encoding="utf-8") as stream:
            samples = [TrainingSample.model_validate_json(line) for line in stream if line.strip()]
        model = cls.train(samples, arguments.output, confidence_level=arguments.confidence)
        print(json.dumps(model.get_model_info(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    LightGBMAgent.main()
