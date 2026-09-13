import os
import sys
import time
import subprocess

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')


def run_cmd(cmd, step_name):
    print(f"\n" + "="*80)
    print(f"[*] ĐANG THỰC HIỆN: {step_name}")
    print(f"    Lệnh: {' '.join(cmd)}")
    print("="*80)
    t0 = time.time()
    ret = subprocess.run(cmd)
    elapsed = time.time() - t0
    if ret.returncode != 0:
        print(f"[!] CẢNH BÁO / LỖI tại bước: {step_name} (mã lỗi: {ret.returncode})")
        sys.exit(ret.returncode)
    else:
        print(f"[✓] HOÀN TẤT: {step_name} trong {elapsed/60.0:.2f} phút.")


import argparse


def main():
    parser = argparse.ArgumentParser(description="Quy trình thực nghiệm tự động 10 runs (hỗ trợ phân tán theo dataset)")
    parser.add_argument('--dataset', type=str, default='all', choices=['all', 'sdn', 'geant', 'abilene'],
                        help="Dataset cần chạy: 'all', 'sdn', 'geant', hoặc 'abilene' (mặc định: 'all')")
    parser.add_argument('--epochs', type=int, default=200, help="Số epoch tối đa (mặc định: 200)")
    parser.add_argument('--patience', type=int, default=30, help="Early stopping patience (mặc định: 30)")
    parser.add_argument('--runs', type=int, default=10, help="Số lần chạy độc lập (mặc định: 10)")
    parser.add_argument('--skip_existing', action='store_true', default=True,
                        help="Bỏ qua các runs đã hoàn thành (mặc định: True)")
    parser.add_argument('--no_skip_existing', dest='skip_existing', action='store_false',
                        help="Chạy lại từ đầu, không bỏ qua các runs đã có")
    args = parser.parse_args()

    python_exe = sys.executable
    ds = args.dataset
    runs_str = str(args.runs)
    epochs_str = str(args.epochs)
    patience_str = str(args.patience)

    print("\n" + "#"*80)
    print(f"# QUY TRÌNH THỰC NGHIỆM TỰ ĐỘNG {args.runs} LẦN CHẠY (RUNS = {args.runs})")
    print(f"# Cấu hình: Dataset = {ds.upper()} | Max Epochs = {args.epochs} | Early Stopping Patience = {args.patience}")
    print(f"# Tính năng: Skip Existing = {args.skip_existing}")
    print("#"*80)

    skip_flag = ["--skip_existing"] if args.skip_existing else []

    # Bước 1: Huấn luyện STWaveFormer (Global Backbone)
    cmd_b1 = [python_exe, "run_experiments.py", "--model", "STWaveFormer",
              "--epochs", epochs_str, "--patience", patience_str, "--runs", runs_str] + skip_flag
    if ds != 'all':
        cmd_b1.extend(["--dataset", ds])
    run_cmd(cmd_b1, f"BƯỚC 1/5: Huấn luyện ST-WaveFormer ({args.runs} runs, dataset={ds})")

    # Bước 2: Huấn luyện LocalSpatialTCN (Local Branch)
    cmd_b2 = [python_exe, "run_experiments.py", "--model", "LocalSpatialTCN",
              "--epochs", epochs_str, "--patience", patience_str, "--runs", runs_str] + skip_flag
    if ds != 'all':
        cmd_b2.extend(["--dataset", ds])
    run_cmd(cmd_b2, f"BƯỚC 2/5: Huấn luyện LocalSpatialTCN ({args.runs} runs, dataset={ds})")

    # Bước 3: Huấn luyện Module A (Traditional ML Baselines)
    cmd_b3 = [python_exe, "baselines_ml/run_ml_baselines.py", "--runs", runs_str] + skip_flag
    if ds != 'all':
        cmd_b3.extend(["--datasets", ds])
    run_cmd(cmd_b3, f"BƯỚC 3/5: Huấn luyện Baseline ML GBDT ({args.runs} runs, dataset={ds})")

    # Bước 4: Chạy toàn bộ quy trình Ensemble (Precompute Cache, Stacking Gate, Ablation)
    cmd_b4 = [python_exe, "training/run_ensemble.py", "--runs", runs_str, "--epochs", epochs_str,
              "--patience", patience_str] + skip_flag
    if ds != 'all':
        cmd_b4.extend(["--datasets", ds])
    run_cmd(cmd_b4, f"BƯỚC 4/5: Chạy ST-Adaptive-Ensemble Pipeline ({args.runs} runs, dataset={ds})")

    # Bước 5: Cập nhật Báo cáo Thống kê & Biểu đồ
    run_cmd([python_exe, "evaluation/report_generator.py"],
            f"BƯỚC 5/5: Xuất Bảng Tổng Hợp Mean ± Std và 95% CI (dataset={ds})")

    print("\n" + "#"*80)
    print(f"# TẤT CẢ {args.runs} RUNS TRÊN DATASET [{ds.upper()}] ĐÃ HOÀN TẤT THÀNH CÔNG VÀ ĐỒNG BỘ!")
    print("# Kết quả cập nhật tại: results/bang_tong_hop_luan_van.csv")
    print("#"*80)


if __name__ == '__main__':
    main()
