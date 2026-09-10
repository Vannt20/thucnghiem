import math
import torch
import torch.nn as nn
import torch.nn.functional as F

try:
    from Graph_models.st_waveformer import RevIN
except ImportError:
    from st_waveformer import RevIN


class SpatialDilatedTCN(nn.Module):
    """
    Nhánh Cục bộ nâng cấp: Spatial-aware Dilated TCN.
    Tối ưu hóa toàn diện theo 4 nguyên lý khoa học:
    1. Trích xuất đặc trưng thời gian tuần hoàn liên tục (Continuous Temporal Features):
       - Tích hợp sin/cos(Time-of-day) và sin/cos(Day-of-week) triệt tiêu gián đoạn biên.
    2. Bộ lọc tích chập giãn nở đa tỷ lệ (Multi-Scale Dilated TCN d=1, 2, 4) trích xuất vi động lực ngắn hạn.
    3. Trao đổi không gian Topology-Masked Adaptive Adjacency:
       - Ma trận kề thích ứng học được được ràng buộc chặt chẽ bởi Topology vật lý cục bộ (Top-k sparse),
         ngăn chặn triệt để hiện tượng rò rỉ liên kết toàn cục (Global Bleed / Over-smoothing).
    4. Cơ chế Residual Last-Value Skip Connection:
       - Dự đoán độ lệch Delta y so với bước thời gian trước x_{t-1}, giúp mô hình hội tụ nhanh và ổn định.
       - Tầng dự báo Delta là Linear thuần túy (không bọc ReLU) để hỗ trợ cả biến động tăng và giảm.
    5. Tích hợp RevIN (Reversible Instance Normalization):
       - Chuẩn hóa chống trôi dạt phân phối (covariate shift) và giải chuẩn hóa đồng bộ.
    """
    def __init__(self, num_nodes: int, hidden_dim=64, emb_dim=10, dropout=0.1, adj_mx=None):
        super(SpatialDilatedTCN, self).__init__()
        self.num_nodes = num_nodes
        self.hidden_dim = hidden_dim

        # 1. RevIN Normalization layer
        self.revin = RevIN(num_features=num_nodes, affine=True)

        # 2. Input Projection: 1 traffic channel + 4 temporal channels (tod_sin, tod_cos, dow_sin, dow_cos) = 5 channels
        self.in_proj = nn.Conv1d(5, hidden_dim, kernel_size=1)

        # 3. Multi-scale Dilated Temporal Convolutions (d=1, 2, 4)
        self.conv_d1 = nn.Conv1d(hidden_dim, hidden_dim, kernel_size=3, padding=1, dilation=1)
        self.conv_d2 = nn.Conv1d(hidden_dim, hidden_dim, kernel_size=3, padding=2, dilation=2)
        self.conv_d4 = nn.Conv1d(hidden_dim, hidden_dim, kernel_size=3, padding=4, dilation=4)

        # 4. Spatial Graph Interaction (Topology-Masked Awareness)
        self.node_emb1 = nn.Parameter(torch.randn(num_nodes, emb_dim))
        self.node_emb2 = nn.Parameter(torch.randn(num_nodes, emb_dim))
        
        if adj_mx is not None:
            self.register_buffer('static_adj', torch.as_tensor(adj_mx, dtype=torch.float32))
        else:
            self.static_adj = None

        self.spatial_proj = nn.Sequential(
            nn.Linear(hidden_dim * 3, hidden_dim * 2),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim * 2, hidden_dim)
        )

        # 5. Output Delta Projection Head (Linear thuần túy ở lớp cuối - KHÔNG bọc ReLU)
        self.out_head = nn.Sequential(
            nn.Linear(hidden_dim, hidden_dim // 2),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim // 2, 1)
        )
        # Zero-Init cho tầng cuối: tại Epoch 0, delta = 0 giúp mô hình bắt đầu ngay
        # từ baseline tự thân hoàn hảo (Persistence) mà không bị nhiễu ngẫu nhiên
        nn.init.zeros_(self.out_head[-1].weight)
        nn.init.zeros_(self.out_head[-1].bias)

    def forward(self, x):
        # x: [B, T, N, C] hoặc [B, T, N]
        if x.dim() == 4:
            x_val = x[:, :, :, 0] # [B, T, N]
            has_time = (x.size(-1) >= 3)
        else:
            x_val = x
            has_time = False

        B, T, N = x_val.shape

        # B1. Chuẩn hóa chống trôi dạt RevIN cho kênh lưu lượng
        x_norm = self.revin(x_val, mode='norm') # [B, T, N]

        # B2. Chuẩn bị đặc trưng thời gian (TOD/DOW)
        if has_time:
            tod = x[:, :, :, 1] # [B, T, N]
            dow = x[:, :, :, 2] # [B, T, N]
            tod_sin = torch.sin(2.0 * math.pi * tod)
            tod_cos = torch.cos(2.0 * math.pi * tod)
            dow_sin = torch.sin(2.0 * math.pi * dow)
            dow_cos = torch.cos(2.0 * math.pi * dow)
            x_feat = torch.stack([x_norm, tod_sin, tod_cos, dow_sin, dow_cos], dim=-1) # [B, T, N, 5]
        else:
            zeros = torch.zeros(B, T, N, 4, device=x.device, dtype=x.dtype)
            x_feat = torch.cat([x_norm.unsqueeze(-1), zeros], dim=-1) # [B, T, N, 5]

        # B3. Chiếu đầu vào và Multi-scale Dilated Convolutions
        # Chuyển đổi thành [B * N, 5, T] để trích xuất vi biến động độc lập cho từng luồng
        x_in = x_feat.permute(0, 2, 3, 1).contiguous().view(B * N, 5, T)
        h_in = F.gelu(self.in_proj(x_in)) # [B * N, H, T]

        c1 = F.gelu(self.conv_d1(h_in))   # [B * N, H, T]
        c2 = F.gelu(self.conv_d2(h_in))   # [B * N, H, T]
        c4 = F.gelu(self.conv_d4(h_in))   # [B * N, H, T]

        c_cat = torch.cat([c1, c2, c4], dim=1) # [B * N, 3*H, T]
        c_last = c_cat[:, :, -1]               # [B * N, 3*H]
        c_feat = c_last.view(B, N, 3 * self.hidden_dim) # [B, N, 3*H]

        # B4. Spatial Graph Interaction (Topology-Masked Adaptive Adjacency)
        A_adapt = F.softmax(F.relu(torch.mm(self.node_emb1, self.node_emb2.T)), dim=-1) # [N, N]
        if self.static_adj is not None:
            # Topology Masking: Ràng buộc thích ứng trong phạm vi lân cận vật lý Top-k
            topo_mask = (self.static_adj > 0).float()
            A_adapt_masked = A_adapt * topo_mask
            A_adapt_norm = A_adapt_masked / (A_adapt_masked.sum(dim=-1, keepdim=True) + 1e-8)
            A = 0.5 * A_adapt_norm + 0.5 * self.static_adj
        else:
            A = A_adapt

        # Lan truyền không gian cục bộ: [B, N, 3*H] -> [B, N, H]
        h_local = self.spatial_proj(c_feat) # [B, N, H]
        h_spatial = torch.matmul(A, h_local) # [B, N, H]
        h_combined = h_local + h_spatial     # Residual connection

        # B5. Dự đoán độ lệch Delta y (Linear head)
        delta_norm = self.out_head(h_combined).squeeze(-1) # [B, N]

        # B6. Residual Last-Value Skip Connection: y_{t} = x_{t-1} + Delta y
        last_val = x_norm[:, -1, :] # [B, N]
        out_norm = last_val + delta_norm # [B, N]

        # B7. Giải chuẩn hóa RevIN khôi phục về thang đo MinMax [0, 1]
        out = self.revin(out_norm, mode='denorm')
        return out


class STWaveNetHybridLocalBranch(nn.Module):
    """
    Nhánh Cục bộ kế thừa trực tiếp từ ST-WaveNet-Hybrid đã huấn luyện hoàn chỉnh.
    Bao gồm RevIN và MultiScaleDilatedTCN (d=1, 2, 4) với đầy đủ trọng số đã tối ưu hóa.
    Nếu được truyền ma trận kề vật lý adj_mx [N, N], mô hình áp dụng thêm
    bộ lọc không gian topo (Topological Spatial Smoothing) giữa các luồng lân cận.
    """
    def __init__(self, hybrid_model, adj_mx=None, spatial_alpha=0.15):
        super(STWaveNetHybridLocalBranch, self).__init__()
        self.hybrid = hybrid_model
        self.spatial_alpha = spatial_alpha
        if adj_mx is not None:
            self.register_buffer('static_adj', torch.as_tensor(adj_mx, dtype=torch.float32))
        else:
            self.static_adj = None

    def forward(self, x):
        x_val = x[:, :, :, 0] if x.dim() == 4 else x
        # B1. Chuẩn hóa chống trôi dạt RevIN
        x_norm = self.hybrid.revin(x_val, mode='norm')
        # B2. Multi-scale Dilated Convolutions
        y_loc_norm = self.hybrid.local_tcn(x_norm) # [B, N]
        
        # B3. Lọc không gian topo nếu có ma trận kề vật lý
        if self.static_adj is not None and self.spatial_alpha > 0:
            y_spatial = torch.matmul(y_loc_norm, self.static_adj.T)
            y_loc_norm = (1.0 - self.spatial_alpha) * y_loc_norm + self.spatial_alpha * y_spatial

        # B4. Giải chuẩn hóa RevIN khôi phục thang đo MinMax [0, 1]
        out = self.hybrid.revin(y_loc_norm, mode='denorm')
        return out


def load_local_branch(ds_key: str, seq_len: int, run_id: int, num_flows: int, adj_flow=None, logs_dir='logs', device='cpu'):
    """
    Nạp Nhánh Cục bộ (Local Branch) đảm bảo LUÔN CÓ TRỌNG SỐ ĐÃ HUẤN LUYỆN:
    1. Ưu tiên 1: Nạp checkpoint từ mô hình độc lập LocalSpatialTCN nếu đã huấn luyện riêng.
    2. Ưu tiên 2: Kế thừa và trích xuất trực tiếp biểu diễn cục bộ từ mô hình ST-WaveNet-Hybrid đã huấn luyện,
                  kết hợp ma trận kề vật lý cấp độ luồng adj_flow.
    3. Tuyệt đối không để xảy ra trường hợp sử dụng mô hình khởi tạo ngẫu nhiên chưa train.
    """
    import os
    # 1. Tìm checkpoint LocalSpatialTCN
    candidate_paths = [
        os.path.join(logs_dir, f"local_spatial_tcn_data_{ds_key}_seq_{seq_len}", f"run_{run_id}", 'best_model.pth'),
        os.path.join(logs_dir, f"localspatialtcn_data_{ds_key}_seq_{seq_len}", f"run_{run_id}", 'best_model.pth'),
    ]
    for ckpt in candidate_paths:
        if os.path.exists(ckpt):
            model = SpatialDilatedTCN(num_nodes=num_flows, hidden_dim=64, adj_mx=adj_flow)
            with open(ckpt, 'rb') as f:
                model.load_state_dict(torch.load(f, map_location=device))
            model.to(device)
            model.eval()
            print(f"      [Local] Đã nạp checkpoint LocalSpatialTCN độc lập: {ckpt}", flush=True)
            return model, "LocalSpatialTCN"

    # 2. Tìm checkpoint ST-WaveNet-Hybrid
    hybrid_ckpt = os.path.join(logs_dir, f"stwavenethybrid_data_{ds_key}_seq_{seq_len}", f"run_{run_id}", 'best_model.pth')
    if os.path.exists(hybrid_ckpt):
        try:
            from Graph_models.st_wavenet_hybrid import STWaveNetHybrid
        except ImportError:
            from st_wavenet_hybrid import STWaveNetHybrid
        hybrid = STWaveNetHybrid(input_dim=num_flows, num_nodes=num_flows, seq_len=seq_len, d_model=64, num_layers=2)
        with open(hybrid_ckpt, 'rb') as f:
            hybrid.load_state_dict(torch.load(f, map_location=device))
        hybrid.to(device)
        hybrid.eval()
        local_model = STWaveNetHybridLocalBranch(hybrid, adj_mx=adj_flow, spatial_alpha=0.15)
        local_model.to(device)
        local_model.eval()
        print(f"      [Local] Kế thừa thành công nhánh Cục bộ đã huấn luyện từ STWaveNetHybrid: {hybrid_ckpt}", flush=True)
        return local_model, "STWaveNetHybrid-Local"

    raise FileNotFoundError(
        f"Không tìm thấy checkpoint đã huấn luyện cho nhánh Local tại:\n"
        f" - {candidate_paths[0]}\n"
        f" - {hybrid_ckpt}\n"
        f"Vui lòng chạy huấn luyện STWaveNetHybrid hoặc LocalSpatialTCN trước khi tạo cache!"
    )

