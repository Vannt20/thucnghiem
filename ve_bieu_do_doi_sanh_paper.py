import os
import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns

plt.rcParams['font.sans-serif'] = 'DejaVu Sans'
plt.rcParams['axes.unicode_minus'] = False

# Comparison data: ST-WaveFormer (5 runs mean) vs Original Paper GWN & BiGRU
data = [
    {"Dataset": "ABILENE", "Model": "ST-WaveFormer (5 runs)", "MSE": 2.460, "MAE": 17.414, "Latency": 3.145},
    {"Dataset": "ABILENE", "Model": "GWN (Bài báo gốc)", "MSE": 6.220, "MAE": 18.318, "Latency": 3.814},
    {"Dataset": "ABILENE", "Model": "BiGRU (Bài báo gốc)", "MSE": 6.188, "MAE": 21.416, "Latency": 1.458},
    {"Dataset": "GEANT", "Model": "ST-WaveFormer (5 runs)", "MSE": 0.864, "MAE": 5.371, "Latency": 3.742},
    {"Dataset": "GEANT", "Model": "GWN (Bài báo gốc)", "MSE": 0.879, "MAE": 5.954, "Latency": 3.651},
    {"Dataset": "GEANT", "Model": "BiGRU (Bài báo gốc)", "MSE": 1.244, "MAE": 11.559, "Latency": 0.417},
    {"Dataset": "SDN", "Model": "ST-WaveFormer (5 runs)", "MSE": 15.564, "MAE": 66.113, "Latency": 3.747},
    {"Dataset": "SDN", "Model": "GWN (Bài báo gốc)", "MSE": 7.936, "MAE": 52.927, "Latency": 2.694},
    {"Dataset": "SDN", "Model": "BiGRU (Bài báo gốc)", "MSE": 12.477, "MAE": 64.776, "Latency": 0.480},
]

df = pd.DataFrame(data)
palette = {"ST-WaveFormer (5 runs)": "#2CA02C", "GWN (Bài báo gốc)": "#1F77B4", "BiGRU (Bài báo gốc)": "#FF7F0E"}

output_dir = 'results'
os.makedirs(output_dir, exist_ok=True)

# 1. Plot MAE
plt.figure(figsize=(10, 5.5))
ax = sns.barplot(data=df, x='Dataset', y='MAE', hue='Model', palette=palette)
plt.title('So sánh sai số MAE (x10^-3) giữa ST-WaveFormer (5 runs) và Nghiên cứu gốc', fontsize=13, fontweight='bold', pad=12)
plt.ylabel('Mean Absolute Error (MAE x 10^-3)', fontsize=11, fontweight='bold')
plt.xlabel('Tập dữ liệu', fontsize=11, fontweight='bold')
plt.grid(axis='y', linestyle='--', alpha=0.7)
plt.legend(title='Mô hình', loc='upper left')

for p in ax.patches:
    val = p.get_height()
    if not np.isnan(val) and val > 0:
        ax.annotate(f'{val:.2f}', (p.get_x() + p.get_width() / 2., val),
                    ha='center', va='bottom', fontsize=9, rotation=0, xytext=(0, 2),
                    textcoords='offset points')

plt.tight_layout()
plt.savefig(os.path.join(output_dir, 'doi_sanh_mae_voi_paper_goc.png'), dpi=300)
plt.close()

# 2. Plot MSE
plt.figure(figsize=(10, 5.5))
ax = sns.barplot(data=df, x='Dataset', y='MSE', hue='Model', palette=palette)
plt.title('So sánh sai số MSE (x10^-3) giữa ST-WaveFormer (5 runs) và Nghiên cứu gốc', fontsize=13, fontweight='bold', pad=12)
plt.ylabel('Mean Squared Error (MSE x 10^-3)', fontsize=11, fontweight='bold')
plt.xlabel('Tập dữ liệu', fontsize=11, fontweight='bold')
plt.grid(axis='y', linestyle='--', alpha=0.7)
plt.legend(title='Mô hình', loc='upper left')

for p in ax.patches:
    val = p.get_height()
    if not np.isnan(val) and val > 0:
        ax.annotate(f'{val:.2f}', (p.get_x() + p.get_width() / 2., val),
                    ha='center', va='bottom', fontsize=9, rotation=0, xytext=(0, 2),
                    textcoords='offset points')

plt.tight_layout()
plt.savefig(os.path.join(output_dir, 'doi_sanh_mse_voi_paper_goc.png'), dpi=300)
plt.close()

# 3. Plot Latency
plt.figure(figsize=(10, 5.5))
ax = sns.barplot(data=df, x='Dataset', y='Latency', hue='Model', palette=palette)
plt.title('So sánh thời gian suy diễn (Inference Latency ms/batch) với Nghiên cứu gốc', fontsize=13, fontweight='bold', pad=12)
plt.ylabel('Thời gian suy diễn (ms)', fontsize=11, fontweight='bold')
plt.xlabel('Tập dữ liệu', fontsize=11, fontweight='bold')
plt.grid(axis='y', linestyle='--', alpha=0.7)
plt.legend(title='Mô hình', loc='upper left')

for p in ax.patches:
    val = p.get_height()
    if not np.isnan(val) and val > 0:
        ax.annotate(f'{val:.2f}', (p.get_x() + p.get_width() / 2., val),
                    ha='center', va='bottom', fontsize=9, rotation=0, xytext=(0, 2),
                    textcoords='offset points')

plt.tight_layout()
plt.savefig(os.path.join(output_dir, 'doi_sanh_latency_voi_paper_goc.png'), dpi=300)
plt.close()

print("-> Generated comparison charts successfully in results/")
