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

def main():
    python_exe = sys.executable
    print("\n" + "#"*80)
    print("# QUY TRÌNH THỰC NGHIỆM TỰ ĐỘNG 10 LẦN CHẠY (RUNS = 10)")
    print("# Cấu hình: Max Epochs = 200 | Early Stopping Patience = 30")
    print("# Tính năng: Tự động dùng kết quả có sẵn (run 0-4), chỉ train tiếp run 5-9")
    print("#"*80)

    # Bước 1: Huấn luyện STWaveFormer (Global Backbone)
    run_cmd([python_exe, "run_experiments.py", "--model", "STWaveFormer",
             "--epochs", "200", "--patience", "30", "--runs", "10", "--skip_existing"],
            "BƯỚC 1/5: Huấn luyện ST-WaveFormer (10 runs, patience=30)")

    # Bước 2: Huấn luyện LocalSpatialTCN (Local Branch)
    run_cmd([python_exe, "run_experiments.py", "--model", "LocalSpatialTCN",
             "--epochs", "200", "--patience", "30", "--runs", "10", "--skip_existing"],
            "BƯỚC 2/5: Huấn luyện LocalSpatialTCN (10 runs, patience=30)")

    # Bước 3: Huấn luyện Module A (Traditional ML Baselines)
    run_cmd([python_exe, "baselines_ml/run_ml_baselines.py", "--runs", "10", "--skip_existing"],
            "BƯỚC 3/5: Huấn luyện Baseline ML GBDT (10 runs)")

    # Bước 4: Chạy toàn bộ quy trình Ensemble (Precompute Cache, Stacking Gate, Ablation)
    run_cmd([python_exe, "training/run_ensemble.py", "--runs", "10", "--epochs", "200",
             "--patience", "30", "--skip_existing"],
            "BƯỚC 4/5: Chạy ST-Adaptive-Ensemble Pipeline (10 runs)")

    # Bước 5: Cập nhật Báo cáo Thống kê & Biểu đồ
    run_cmd([python_exe, "evaluation/report_generator.py"],
            "BƯỚC 5/5: Xuất Bảng Tổng Hợp Mean ± Std và 95% CI (10 runs)")

    print("\n" + "#"*80)
    print("# TẤT CẢ 10 RUNS ĐÃ HOÀN TẤT THÀNH CÔNG VÀ ĐỒNG BỘ!")
    print("# Kết quả cập nhật tại: results/bang_tong_hop_luan_van.csv")
    print("#"*80)

if __name__ == '__main__':
    main()
