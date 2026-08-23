import os
import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')
import time
import argparse
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import Dataset, DataLoader
from sklearn.preprocessing import MinMaxScaler
from tqdm import tqdm, trange

# Add project paths to sys.path robustly for Google Colab / Linux / Windows / Notebooks
current_dir = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.getcwd()
for p in [
    current_dir,
    os.path.join(current_dir, 'Graph_models'),
    os.path.join(current_dir, 'old_Graph_models')
]:
    abs_p = os.path.abspath(p)
    if abs_p not in sys.path:
        sys.path.insert(0, abs_p)

try:
    from Graph_models.gwn import GWNet
    from Graph_models.dcrnn import DCRNNModel
    from Graph_models.st_waveformer import STWaveFormer, StackingEnsemble
except ImportError:
    try:
        from gwn import GWNet
        from dcrnn import DCRNNModel
        from st_waveformer import STWaveFormer, StackingEnsemble
    except ImportError:
        from old_Graph_models.gwn import GWNet
        from old_Graph_models.dcrnn import DCRNNModel
        from Graph_models.st_waveformer import STWaveFormer, StackingEnsemble

# Define device
device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

# ==============================================================================
# Model Definitions
# ==============================================================================

class LSTM_TM(nn.Module):
    def __init__(self, input_dim, hidden_dim=100, seq_len=24):
        super(LSTM_TM, self).__init__()
        self.lstm = nn.LSTM(input_size=input_dim, hidden_size=hidden_dim, batch_first=True)
        self.fc = nn.Linear(hidden_dim, input_dim)

    def forward(self, x):
        if x.dim() == 4:
            x = x[:, :, :, 0]
        out, _ = self.lstm(x)
        return self.fc(out[:, -1])


class BiLSTM_TM(nn.Module):
    def __init__(self, input_dim, hidden_dim=100, seq_len=24):
        super(BiLSTM_TM, self).__init__()
        self.lstm = nn.LSTM(input_size=input_dim, hidden_size=hidden_dim, batch_first=True, bidirectional=True)
        self.fc = nn.Linear(2 * hidden_dim, input_dim)

    def forward(self, x):
        if x.dim() == 4:
            x = x[:, :, :, 0]
        out, _ = self.lstm(x)
        return self.fc(out[:, -1])


class GRU_TM(nn.Module):
    def __init__(self, input_dim, hidden_dim=100, seq_len=24):
        super(GRU_TM, self).__init__()
        self.gru = nn.GRU(input_size=input_dim, hidden_size=hidden_dim, batch_first=True)
        self.fc = nn.Linear(hidden_dim, input_dim)

    def forward(self, x):
        if x.dim() == 4:
            x = x[:, :, :, 0]
        out, _ = self.gru(x)
        return self.fc(out[:, -1])


class BiGRU_TM(nn.Module):
    def __init__(self, input_dim, hidden_dim=100, seq_len=24):
        super(BiGRU_TM, self).__init__()
        self.gru = nn.GRU(input_size=input_dim, hidden_size=hidden_dim, batch_first=True, bidirectional=True)
        self.fc = nn.Linear(2 * hidden_dim, input_dim)

    def forward(self, x):
        if x.dim() == 4:
            x = x[:, :, :, 0]
        out, _ = self.gru(x)
        return self.fc(out[:, -1])


# ==============================================================================
# Metric Calculations
# ==============================================================================
EPS = 1e-8

def calc_metrics(preds, labels):
    preds = preds.float()
    labels = labels.float()
    rse = torch.sum((preds - labels) ** 2) / (torch.sum((labels - torch.mean(labels)) ** 2) + EPS)
    mae = torch.mean(torch.abs(preds - labels))
    mse = torch.mean((preds - labels) ** 2)
    rmse = torch.sqrt(mse)
    mape = torch.mean(torch.abs((preds - labels) / (labels + EPS)))
    return rse, mae, mse, mape, rmse


# ==============================================================================
# Dataset and Data Loading
# ==============================================================================

class TrafficDataset(Dataset):
    def __init__(self, model_name, x, y, device):
        self.model_name = model_name.lower()
        self.device = device
        self.x = torch.tensor(x, dtype=torch.float32)
        self.y = torch.tensor(y, dtype=torch.float32)
        self.nsample = self.x.shape[0]

    def __len__(self):
        return self.nsample

    def __getitem__(self, idx):
        x = self.x[idx]
        y = self.y[idx]

        if self.model_name in ['stwaveformer', 'st-waveformer', 'stwaveformerensemble', 'st-waveformer-ensemble']:
            # STWaveFormer handles multi-channel input [seq_len, num_flows, channels]
            pass
        else:
            if x.dim() == 3:
                x = x[:, :, 0]
            if self.model_name == 'gwn':
                x = torch.unsqueeze(x, -1)

        if len(y.shape) > 1 and y.shape[0] == 1:
            y = torch.squeeze(y, dim=0)

        return {'x': x.to(self.device), 'y': y.to(self.device)}


def prepare_dataset(dataset_name, in_seq_len, out_seq_len=1, batch_size=64, model_name='lstm'):
    data_dir = os.path.join(os.path.dirname(__file__), 'data')
    fpath = os.path.join(data_dir, f'{dataset_name}.csv')
    if not os.path.exists(fpath):
        fpath = os.path.join(data_dir, f'{dataset_name.upper()}.csv')

    df = pd.read_csv(fpath, parse_dates=['time'])
    df = df.set_index(['time'])

    total_steps = len(df)
    train_size = int(total_steps * 0.7)
    val_size = int(total_steps * 0.1)

    train_df = df.iloc[0:train_size]
    val_df = df.iloc[train_size:train_size + val_size]
    test_df = df.iloc[train_size + val_size:]

    scaler = MinMaxScaler(feature_range=(0, 1))
    train_norm = scaler.fit_transform(train_df)
    val_norm = scaler.transform(val_df)
    test_norm = scaler.transform(test_df)

    # Context features: Time-of-day (0-1) and Day-of-week (0-1)
    time_idx = df.index
    tod = (time_idx.hour * 60.0 + time_idx.minute) / 1440.0
    dow = time_idx.dayofweek / 7.0

    num_flows = train_norm.shape[1]
    tod_arr = np.tile(tod.values[:, None], (1, num_flows))
    dow_arr = np.tile(dow.values[:, None], (1, num_flows))

    # Stack channels: [0]=traffic, [1]=tod, [2]=dow
    comb_train = np.stack([train_norm, tod_arr[0:train_size], dow_arr[0:train_size]], axis=-1).astype(np.float32)
    comb_val = np.stack([val_norm, tod_arr[train_size:train_size + val_size], dow_arr[train_size:train_size + val_size]], axis=-1).astype(np.float32)
    comb_test = np.stack([test_norm, tod_arr[train_size + val_size:], dow_arr[train_size + val_size:]], axis=-1).astype(np.float32)

    def create_sliding_window(arr, seq_in, seq_out):
        xs, ys = [], []
        for i in range(len(arr) - seq_in - seq_out + 1):
            xs.append(arr[i : i + seq_in])
            ys.append(arr[i + seq_in : i + seq_in + seq_out, :, 0]) # target is traffic volume
        return np.array(xs, dtype=np.float32), np.array(ys, dtype=np.float32)

    x_train, y_train = create_sliding_window(comb_train, in_seq_len, out_seq_len)
    x_val, y_val = create_sliding_window(comb_val, in_seq_len, out_seq_len)
    x_test, y_test = create_sliding_window(comb_test, in_seq_len, out_seq_len)

    train_dataset = TrafficDataset(model_name, x_train, y_train, device)
    val_dataset = TrafficDataset(model_name, x_val, y_val, device)
    test_dataset = TrafficDataset(model_name, x_test, y_test, device)

    train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True)
    val_loader = DataLoader(val_dataset, batch_size=batch_size, shuffle=False)
    test_loader = DataLoader(test_dataset, batch_size=batch_size, shuffle=False)

    return train_loader, val_loader, test_loader, scaler, num_flows


# ==============================================================================
# Model Factory
# ==============================================================================

def build_model(model_name, dataset_name, in_seq_len, num_flows, num_nodes):
    m_name = model_name.lower().replace('-', '')
    if m_name == 'lstm':
        return LSTM_TM(input_dim=num_flows, hidden_dim=100, seq_len=in_seq_len)
    elif m_name == 'bilstm':
        return BiLSTM_TM(input_dim=num_flows, hidden_dim=100, seq_len=in_seq_len)
    elif m_name == 'gru':
        return GRU_TM(input_dim=num_flows, hidden_dim=100, seq_len=in_seq_len)
    elif m_name == 'bigru':
        return BiGRU_TM(input_dim=num_flows, hidden_dim=100, seq_len=in_seq_len)
    elif m_name == 'gwn':
        return GWNet.from_args(
            supports=None, aptinit=None, num_nodes=num_nodes, num_flows=num_flows, in_dim=1,
            apt_size=10, out_seq_len=1, in_seq_len=in_seq_len, hidden=32,
            stride=2, kernel_size=2, blocks=2, layers=2,
            cat_feat_gc=False, do_graph_conv=True, addaptadj=True,
            dropout=0.5, batch_size=64, device=str(device), verbose=False
        )
    elif m_name == 'dcrnn':
        adj_path = os.path.join(os.path.dirname(__file__), 'data', f'{dataset_name}_adj.npy')
        if not os.path.exists(adj_path):
            adj_mx = np.eye(num_nodes, dtype=np.float32)
        else:
            adj_mx = np.load(adj_path)
        return DCRNNModel(adj_mx=adj_mx, seq_len=in_seq_len, nodes=num_nodes, pre_len=1, device=device, num_rnn_layers=2, rnn_units=32)
    elif m_name in ['stwaveformer', 'stwaveformerensemble']:
        return STWaveFormer(input_dim=num_flows, num_nodes=num_nodes, seq_len=in_seq_len, d_model=64, num_layers=2)
    else:
        raise ValueError(f"Unsupported model: {model_name}")


# ==============================================================================
# Training & Testing Functions
# ==============================================================================

def train_and_eval_model(model, train_loader, val_loader, test_loader, epochs=200, patience=30, lr=1e-3, weight_decay=1e-4, logdir='logs', model_name='lstm'):
    logdir = os.path.abspath(logdir)
    os.makedirs(logdir, exist_ok=True)
    m_name = model_name.lower().replace('-', '')

    if m_name in ['stwaveformer', 'stwaveformerensemble']:
        lossfn = nn.SmoothL1Loss(beta=0.01)
    else:
        lossfn = nn.MSELoss()

    optimizer = optim.Adam(model.parameters(), lr=lr, weight_decay=weight_decay)
    
    if m_name in ['stwaveformer', 'stwaveformerensemble']:
        scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs, eta_min=1e-6)
    else:
        scheduler = optim.lr_scheduler.LambdaLR(optimizer, lr_lambda=lambda ep: (0.97) ** ep)

    best_val_loss = float('inf')
    best_model_path = os.path.join(logdir, 'best_model.pth')
    os.makedirs(os.path.dirname(best_model_path), exist_ok=True)
    patience_counter = 0
    history = []

    model.to(device)

    for epoch in range(epochs):
        model.train()
        train_losses = []
        for batch in train_loader:
            x, y = batch['x'], batch['y']
            optimizer.zero_grad()
            out = model(x)
            if out.dim() == 4:
                out = out[:, :, :, -1]
            if out.dim() == 3 and out.size(1) == 1:
                out = out.squeeze(1)
            loss = lossfn(out, y)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            train_losses.append(loss.item())

        scheduler.step()

        # Validation
        model.eval()
        val_losses = []
        with torch.no_grad():
            for batch in val_loader:
                x, y = batch['x'], batch['y']
                out = model(x)
                if out.dim() == 4:
                    out = out[:, :, :, -1]
                if out.dim() == 3 and out.size(1) == 1:
                    out = out.squeeze(1)
                v_loss = lossfn(out, y)
                val_losses.append(v_loss.item())

        mean_tr_loss = np.mean(train_losses)
        mean_val_loss = np.mean(val_losses)

        history.append({'epoch': epoch, 'train_loss': mean_tr_loss, 'val_loss': mean_val_loss})

        if mean_val_loss < best_val_loss:
            best_val_loss = mean_val_loss
            patience_counter = 0
            with open(best_model_path, 'wb') as f:
                torch.save(model.state_dict(), f)
            saved_str = "*"
        else:
            patience_counter += 1
            saved_str = " "

        print(f"  Epoch {epoch+1:03d}/{epochs} | Train Loss: {mean_tr_loss:.6f} | Val Loss: {mean_val_loss:.6f} (Best: {best_val_loss:.6f}){saved_str} | Patience: {patience_counter}/{patience}", flush=True)

        if patience_counter >= patience:
            print(f"  --> Early stopping triggered at epoch {epoch+1}", flush=True)
            break

    # Save history
    pd.DataFrame(history).to_csv(os.path.join(logdir, 'train_metrics.csv'), index=False)

    # Load best model for testing
    if os.path.exists(best_model_path):
        with open(best_model_path, 'rb') as f:
            model.load_state_dict(torch.load(f, map_location=device))

    # Test evaluation & inference timing
    model.eval()
    all_preds, all_reals = [], []
    inference_times = []

    with torch.no_grad():
        for batch in test_loader:
            x, y = batch['x'], batch['y']
            start_t = time.perf_counter()
            out = model(x)
            end_t = time.perf_counter()
            inference_times.append((end_t - start_t) * 1000.0) # in ms

            if out.dim() == 4:
                out = out[:, :, :, -1]
            if out.dim() == 3 and out.size(1) == 1:
                out = out.squeeze(1)

            out = torch.clamp(out, min=0.0, max=1.0)
            all_preds.append(out.cpu())
            all_reals.append(y.cpu())

    y_hat = torch.cat(all_preds, dim=0)
    y_real = torch.cat(all_reals, dim=0)

    rse, mae, mse, mape, rmse = calc_metrics(y_hat, y_real)
    avg_inference_time = np.mean(inference_times) # ms

    test_metrics = {
        'mse': float(mse.item()),
        'mae': float(mae.item()),
        'rmse': float(rmse.item()),
        'rse': float(rse.item()),
        'mape': float(mape.item()),
        'inference_time_ms': float(avg_inference_time)
    }

    # Save test logs
    test_df = pd.DataFrame([test_metrics])
    test_df.to_csv(os.path.join(logdir, 'test_metrics.csv'), index=False)
    np.save(os.path.join(logdir, 'y_real_data.npy'), y_real.numpy())
    np.save(os.path.join(logdir, 'y_pred_data.npy'), y_hat.numpy())

    return test_metrics


def train_and_eval_ensemble(dataset_name, seq_len, num_flows, num_nodes, epochs=50, patience=30, logdir='logs'):
    """
    Trains Stacking Ensemble meta-learner using base model predictions (BiGRU, GWN, STWaveFormer).
    """
    print("\n---> Đang huấn luyện Mô hình Học tập hợp (Stacking Ensemble)...", flush=True)
    logdir = os.path.abspath(logdir)
    os.makedirs(logdir, exist_ok=True)

    base_models = ['BiGRU', 'GWN', 'STWaveFormer']
    base_preds_train = []
    base_preds_test = []
    y_train_target = None
    y_test_target = None

    for b_name in base_models:
        train_loader, val_loader, test_loader, _, _ = prepare_dataset(
            dataset_name, in_seq_len=seq_len, out_seq_len=1, batch_size=64, model_name=b_name
        )
        model = build_model(b_name, dataset_name, seq_len, num_flows, num_nodes)
        b_logdir = os.path.join(os.path.dirname(__file__), 'logs', f"{b_name.lower()}_data_{dataset_name}_seq_{seq_len}", "run_0")
        best_path = os.path.join(b_logdir, 'best_model.pth')

        if not os.path.exists(best_path):
            train_and_eval_model(model, train_loader, val_loader, test_loader, epochs=min(epochs, 50), patience=patience, logdir=b_logdir, model_name=b_name)
        else:
            model.load_state_dict(torch.load(best_path, map_location=device))

        model.to(device)
        model.eval()

        # Collect train preds
        tr_preds = []
        tr_reals = []
        with torch.no_grad():
            for batch in train_loader:
                out = model(batch['x'])
                if out.dim() == 4: out = out[:, :, :, -1]
                if out.dim() == 3 and out.size(1) == 1: out = out.squeeze(1)
                tr_preds.append(out.cpu())
                tr_reals.append(batch['y'].cpu())
        base_preds_train.append(torch.cat(tr_preds, dim=0))
        if y_train_target is None:
            y_train_target = torch.cat(tr_reals, dim=0)

        # Collect test preds
        te_preds = []
        te_reals = []
        with torch.no_grad():
            for batch in test_loader:
                out = model(batch['x'])
                if out.dim() == 4: out = out[:, :, :, -1]
                if out.dim() == 3 and out.size(1) == 1: out = out.squeeze(1)
                te_preds.append(out.cpu())
                te_reals.append(batch['y'].cpu())
        base_preds_test.append(torch.cat(te_preds, dim=0))
        if y_test_target is None:
            y_test_target = torch.cat(te_reals, dim=0)

    # Train Meta-Learner
    ensemble_meta = StackingEnsemble(input_dim=num_flows, num_models=len(base_models)).to(device)
    optimizer = optim.Adam(ensemble_meta.parameters(), lr=1e-3, weight_decay=1e-4)
    lossfn = nn.SmoothL1Loss(beta=0.01)

    X_meta_train = [p.to(device) for p in base_preds_train]
    y_meta_train = y_train_target.to(device)
    X_meta_test = [p.to(device) for p in base_preds_test]
    y_meta_test = y_test_target.to(device)

    for ep in range(epochs):
        ensemble_meta.train()
        optimizer.zero_grad()
        out = ensemble_meta(X_meta_train)
        loss = lossfn(out, y_meta_train)
        loss.backward()
        optimizer.step()

    # Evaluation on Test set
    ensemble_meta.eval()
    start_t = time.perf_counter()
    with torch.no_grad():
        out_test = ensemble_meta(X_meta_test)
    end_t = time.perf_counter()
    inf_time = ((end_t - start_t) * 1000.0) / len(y_meta_test) # per batch approx

    out_test = torch.clamp(out_test, min=0.0, max=1.0)
    rse, mae, mse, mape, rmse = calc_metrics(out_test.cpu(), y_test_target)

    test_metrics = {
        'mse': float(mse.item()),
        'mae': float(mae.item()),
        'rmse': float(rmse.item()),
        'rse': float(rse.item()),
        'mape': float(mape.item()),
        'inference_time_ms': float(inf_time)
    }

    test_df = pd.DataFrame([test_metrics])
    test_df.to_csv(os.path.join(logdir, 'test_metrics.csv'), index=False)
    return test_metrics


# ==============================================================================
# Main Experiment Runner
# ==============================================================================

DATASET_CONFIGS = {
    'sdn': {'nodes': 14, 'flows': 196, 'seq_len': 60},
    'geant': {'nodes': 23, 'flows': 529, 'seq_len': 24},
    'abilene': {'nodes': 12, 'flows': 144, 'seq_len': 24}
}

MODELS_LIST = ['BiGRU', 'GWN', 'STWaveFormer', 'STWaveFormerEnsemble']

def run_all_experiments(datasets=None, models=None, epochs=200, patience=30, runs=1):
    if datasets is None:
        datasets = ['sdn', 'geant', 'abilene']
    if models is None:
        models = MODELS_LIST

    results_dir = os.path.join(os.path.dirname(__file__), 'results')
    os.makedirs(results_dir, exist_ok=True)

    summary_records = []

    print("=" * 80, flush=True)
    print(" CHẠY THỰC NGHIỆM ĐÁNH GIÁ MÔ HÌNH ST-WAVEFORMER VÀ STACKING ENSEMBLE ", flush=True)
    print(f" Device: {device} | Datasets: {datasets} | Models: {models} | Epochs: {epochs}", flush=True)
    print("=" * 80, flush=True)

    for ds in datasets:
        cfg = DATASET_CONFIGS[ds]
        seq_len = cfg['seq_len']
        num_nodes = cfg['nodes']
        num_flows = cfg['flows']

        print(f"\n==================== DATASET: {ds.upper()} (nodes={num_nodes}, flows={num_flows}, seq_len={seq_len}) ====================", flush=True)

        for m_name in models:
            print(f"\n---> Đang thực nghiệm mô hình: {m_name} trên tập {ds.upper()}...", flush=True)
            run_metrics = []

            for run_id in range(runs):
                logdir = os.path.join(os.path.dirname(__file__), 'logs', f"{m_name.lower().replace('-','')}_data_{ds}_seq_{seq_len}", f"run_{run_id}")
                
                if m_name.lower().replace('-', '') == 'stwaveformerensemble':
                    metrics = train_and_eval_ensemble(
                        ds, seq_len, num_flows, num_nodes, epochs=min(epochs, 50), patience=patience, logdir=logdir
                    )
                else:
                    train_loader, val_loader, test_loader, scaler, _ = prepare_dataset(
                        ds, in_seq_len=seq_len, out_seq_len=1, batch_size=64, model_name=m_name
                    )
                    model = build_model(m_name, ds, seq_len, num_flows, num_nodes)
                    metrics = train_and_eval_model(
                        model, train_loader, val_loader, test_loader,
                        epochs=epochs, patience=patience, logdir=logdir, model_name=m_name
                    )
                
                metrics['run'] = run_id
                metrics['seq_len'] = seq_len
                run_metrics.append(metrics)

            df_runs = pd.DataFrame(run_metrics)
            out_csv = os.path.join(results_dir, f"results_{m_name.replace('-', '')}_data_{ds}.csv")
            df_runs.to_csv(out_csv, index=False)

            mean_mse = df_runs['mse'].mean()
            mean_mae = df_runs['mae'].mean()
            mean_rmse = df_runs['rmse'].mean()
            mean_rse = df_runs['rse'].mean()
            mean_mape = df_runs['mape'].mean()
            mean_time = df_runs['inference_time_ms'].mean()

            summary_records.append({
                'Dataset': ds.upper(),
                'Model': m_name,
                'Seq_Len': seq_len,
                'MSE (x10^-3)': mean_mse * 1000.0,
                'MAE (x10^-3)': mean_mae * 1000.0,
                'RMSE': mean_rmse,
                'RSE': mean_rse,
                'MAPE': mean_mape,
                'Inference Time (ms)': mean_time
            })

            print(f"[*] Kết quả {m_name} trên {ds.upper()}: MSE={mean_mse*1000.0:.3f}e-3 | MAE={mean_mae*1000.0:.3f}e-3 | Time={mean_time:.3f} ms", flush=True)

    # Tổng hợp bảng kết quả danh gia mo hinh
    try:
        from plot_comparisons import collect_results_from_dir
        summary_df = collect_results_from_dir(results_dir=results_dir)
        summary_csv = os.path.join(results_dir, 'bang_ket_qua_danh_gia_mo_hinh.csv')
    except Exception as e:
        summary_df = pd.DataFrame(summary_records)
        summary_csv = os.path.join(results_dir, 'bang_ket_qua_danh_gia_mo_hinh.csv')
        summary_df.to_csv(summary_csv, index=False)

    print("\n" + "=" * 90, flush=True)
    print(" BẢNG TỔNG HỢP KẾT QUẢ ĐÁNH GIÁ MÔ HÌNH (THỦ CÔNG / CỦA LUẬN VĂN) ", flush=True)
    print("=" * 90, flush=True)
    print(summary_df.to_string(index=False), flush=True)
    print("=" * 90, flush=True)
    print(f"-> Đã lưu bảng kết quả tổng hợp tích lũy tại: {summary_csv}", flush=True)

    return summary_df


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Network Traffic Prediction & Model Evaluation")
    parser.add_argument('--dataset', type=str, default='all', choices=['all', 'sdn', 'geant', 'abilene'])
    parser.add_argument('--model', type=str, default='all', choices=['all', 'LSTM', 'BiLSTM', 'GRU', 'BiGRU', 'GWN', 'DCRNN', 'STWaveFormer', 'STWaveFormerEnsemble'])
    parser.add_argument('--epochs', type=int, default=200, help='Max training epochs per model')
    parser.add_argument('--patience', type=int, default=30, help='Early stopping patience')
    parser.add_argument('--runs', type=int, default=1, help='Number of repeated runs')
    parser.add_argument('--quick_check', action='store_true', help='Run 2 epochs for quick pipeline verification')

    args = parser.parse_args()

    ds_list = [args.dataset] if args.dataset != 'all' else ['sdn', 'geant', 'abilene']
    m_list = [args.model] if args.model != 'all' else MODELS_LIST

    if args.quick_check:
        print("=== CHẾ ĐỘ KIỂM TRA NHANH (QUICK CHECK) ===")
        run_all_experiments(datasets=ds_list, models=m_list, epochs=2, patience=2, runs=1)
    else:
        run_all_experiments(datasets=ds_list, models=m_list, epochs=args.epochs, patience=args.patience, runs=args.runs)
