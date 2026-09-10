import time
import numpy as np
import torch

EPS = 1e-8


def calc_metrics_numpy(preds, labels):
    """
    Tính các chỉ số đánh giá bằng NumPy:
    - MSE, MAE, RMSE, RSE, MAPE
    """
    preds = np.asarray(preds, dtype=np.float64)
    labels = np.asarray(labels, dtype=np.float64)
    
    mse = np.mean((preds - labels) ** 2)
    mae = np.mean(np.abs(preds - labels))
    rmse = np.sqrt(mse)
    
    denom_rse = np.sum((labels - np.mean(labels)) ** 2) + EPS
    rse = np.sum((preds - labels) ** 2) / denom_rse
    
    mape = np.mean(np.abs((preds - labels) / (labels + EPS)))
    
    return {
        'mse': float(mse),
        'mae': float(mae),
        'rmse': float(rmse),
        'rse': float(rse),
        'mape': float(mape)
    }


def measure_inference_time(predict_fn, X, batch_size=64, n_warmup=2, n_runs=5):
    """
    Đo thời gian suy diễn trung bình (ms / batch).
    """
    N_samples = len(X)
    if N_samples == 0:
        return 0.0
        
    num_batches = int(np.ceil(N_samples / batch_size))
    # Chạy warmup
    for _ in range(n_warmup):
        sample_batch = X[:batch_size]
        predict_fn(sample_batch)
        
    # Đo thời gian
    times = []
    for _ in range(n_runs):
        idx = np.random.randint(0, max(1, N_samples - batch_size))
        batch = X[idx : idx + batch_size]
        t0 = time.perf_counter()
        predict_fn(batch)
        t1 = time.perf_counter()
        times.append((t1 - t0) * 1000.0) # in ms
        
    return float(np.mean(times))
