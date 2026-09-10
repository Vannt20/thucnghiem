import os
import joblib
import numpy as np
from baselines_ml.metrics import calc_metrics_numpy, measure_inference_time


class CatBoostBaseline:
    """
    CatBoost Regressor theo chiến lược multi_output: shared_model:
    - 1 model duy nhất cho toàn bộ N luồng OD.
    - flow_id ở cột 0 là native cat_features.
    - Ordered Boosting chống overfit cực mạnh trên chuỗi ngắn (như SDN).
    - Hàm mất mát Huber delta=0.01.
    """
    def __init__(self, loss_function='Huber:delta=0.01', depth=6,
                 learning_rate=0.05, iterations=1000, early_stopping_rounds=30,
                 random_seed=42, thread_count=-1, **kwargs):
        self.params = {
            'loss_function': loss_function,
            'depth': depth,
            'learning_rate': learning_rate,
            'iterations': iterations,
            'early_stopping_rounds': early_stopping_rounds,
            'random_seed': random_seed,
            'thread_count': thread_count,
            'verbose': 0,
            **kwargs
        }
        self.model = None

    def fit(self, X_train, y_train, X_val=None, y_val=None):
        from catboost import CatBoostRegressor

        self.model = CatBoostRegressor(**self.params)
        
        # Chuyển cột flow_id sang int để CatBoost nhận làm cat_features
        cat_features = [0]
        
        eval_set = None
        if X_val is not None and y_val is not None:
            eval_set = (X_val, y_val)

        self.model.fit(
            X_train, y_train,
            eval_set=eval_set,
            cat_features=cat_features,
            verbose=False
        )
        return self

    def predict(self, X):
        preds = self.model.predict(X)
        return np.clip(preds, 0.0, None)

    def evaluate(self, X_test, y_test, batch_size=64):
        preds = self.predict(X_test)
        metrics = calc_metrics_numpy(preds, y_test)
        inf_time = measure_inference_time(lambda b: self.predict(b), X_test, batch_size=batch_size)
        metrics['inference_time_ms'] = inf_time
        return metrics, preds

    def save(self, filepath):
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        self.model.save_model(filepath)

    def load(self, filepath):
        from catboost import CatBoostRegressor
        self.model = CatBoostRegressor()
        self.model.load_model(filepath)
        return self
