import math
import torch
import torch.nn as nn
import torch.nn.functional as F

try:
    from Graph_models.st_waveformer import RevIN, DynamicAdaptiveGCN, STWaveFormerBlock
except ImportError:
    from st_waveformer import RevIN, DynamicAdaptiveGCN, STWaveFormerBlock


class MultiScaleDilatedTCN(nn.Module):
    """
    Nhánh trích xuất đặc trưng cục bộ đa tỷ lệ (Multi-Scale Dilated TCN).
    Áp dụng các bộ lọc tích chập 1D đa thang độ (d=1, d=2, d=4) chia sẻ trọng số trên tất cả các luồng lưu lượng.
    Đóng vai trò như bộ lọc thông thấp (low-pass smoothing filter) và bắt vi biến động ngắn hạn,
    khắc phục triệt để hiện tượng quá nhạy cảm trước các gai xung đột biến của Attention trên tập dữ liệu chu kỳ ngắn (như SDN 1 phút).
    """
    def __init__(self, hidden_dim=32, dropout=0.1):
        super(MultiScaleDilatedTCN, self).__init__()
        # in_channels=1 cho từng luồng lưu lượng độc lập
        self.conv_d1 = nn.Conv1d(1, hidden_dim, kernel_size=3, padding=1, dilation=1)
        self.conv_d2 = nn.Conv1d(1, hidden_dim, kernel_size=3, padding=2, dilation=2)
        self.conv_d4 = nn.Conv1d(1, hidden_dim, kernel_size=3, padding=4, dilation=4)
        
        self.fusion = nn.Sequential(
            nn.Linear(hidden_dim * 3, hidden_dim),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, 1)
        )

    def forward(self, x):
        # x: [B, T, N]
        B, T, N = x.shape
        # Chuyển đổi thành [B * N, 1, T] để lọc chuỗi thời gian cho từng luồng
        x_in = x.permute(0, 2, 1).contiguous().view(B * N, 1, T) # [B * N, 1, T]
        
        c1 = F.gelu(self.conv_d1(x_in)) # [B * N, H, T]
        c2 = F.gelu(self.conv_d2(x_in)) # [B * N, H, T]
        c4 = F.gelu(self.conv_d4(x_in)) # [B * N, H, T]
        
        c_all = torch.cat([c1, c2, c4], dim=1) # [B * N, 3*H, T]
        c_last = c_all[:, :, -1]              # [B * N, 3*H] (lấy trạng thái ở bước thời gian cuối)
        
        out = self.fusion(c_last)             # [B * N, 1]
        out = out.view(B, N)                  # [B, N]
        return out


class ContextualMetaGating(nn.Module):
    """
    Khối điều phối thích ứng (Contextual Meta-Gating Module) lấy cảm hứng từ Reinforcement Policy.
    Đầu vào: Mức độ biến động vi mô (local volatility), xu hướng tăng giảm gần nhất và đặc trưng ngoại cảnh (TOD, DOW).
    Đầu ra: Vector cổng mềm g in [0, 1] quyết định tỷ trọng giữa nhánh Toàn cục (ST-WaveFormer) và Cục bộ (Dilated TCN).
    """
    def __init__(self, hidden_dim=32):
        super(ContextualMetaGating, self).__init__()
        # Input features per flow: [mean, std, last_val, diff_last] (4 dims) + time context (2 dims) = 6 dims
        self.gate_net = nn.Sequential(
            nn.Linear(6, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, 1),
            nn.Sigmoid()
        )
        # Khởi tạo bias cổng dương (+1.5) để ban đầu ưu tiên nhánh ST-WaveFormer (g ~ 0.82),
        # đảm bảo mô hình kế thừa trọn vẹn sức mạnh hiện tại và học dần việc bổ trợ từ nhánh TCN.
        nn.init.constant_(self.gate_net[2].bias, 1.5)

    def forward(self, x_val, time_feats=None):
        # x_val: [B, T, N]
        B, T, N = x_val.shape
        mean_v = x_val.mean(dim=1, keepdim=True) # [B, 1, N]
        std_v = x_val.std(dim=1, keepdim=True) + 1e-6 # [B, 1, N]
        last_v = x_val[:, -1:, :] # [B, 1, N]
        diff_v = last_v - x_val[:, -2:-1, :] if T > 1 else torch.zeros_like(last_v) # [B, 1, N]
        
        stat_feats = torch.cat([mean_v, std_v, last_v, diff_v], dim=1) # [B, 4, N]
        stat_feats = stat_feats.permute(0, 2, 1) # [B, N, 4]
        
        if time_feats is not None:
            # time_feats: [B, T, N, 2] -> lấy mốc cuối [B, N, 2]
            t_last = time_feats[:, -1, :, :] # [B, N, 2]
            feat_in = torch.cat([stat_feats, t_last], dim=-1) # [B, N, 6]
        else:
            dummy_t = torch.zeros(B, N, 2, device=x_val.device)
            feat_in = torch.cat([stat_feats, dummy_t], dim=-1) # [B, N, 6]
            
        gate = self.gate_net(feat_in).squeeze(-1) # [B, N]
        return gate


class STWaveNetHybrid(nn.Module):
    """
    MÔ HÌNH HỌC KẾT HỢP ĐA NHÁNH THÍCH ỨNG: ST-WaveNet-Hybrid
    (Spatio-Temporal Wavelet Hybrid Network with Contextual Meta-Gating)
    
    Cấu trúc 3 thành phần chủ chốt:
    1. Nhánh Toàn cục (Global Branch): ST-WaveFormer (Multi-Head Attention + Dynamic Adaptive GCN + RevIN).
    2. Nhánh Cục bộ (Local Branch): Multi-Scale Dilated TCN học thông thấp và làm mượt gai nhiễu vi mô.
    3. Bộ điều phối thích ứng (Meta-Gating): Điều tiết tỷ trọng động theo biến động luồng mạng.
    """
    def __init__(self, input_dim: int, num_nodes: int, seq_len=24, d_model=64, num_layers=2, nhead=4, dropout=0.1):
        super(STWaveNetHybrid, self).__init__()
        self.input_dim = input_dim
        self.num_nodes = num_nodes
        self.seq_len = seq_len
        self.d_model = d_model
        
        # 1. RevIN Normalization
        self.revin = RevIN(num_features=input_dim, affine=True)
        
        # 2. Nhánh Toàn cục (Global Macro Branch): ST-WaveFormer Backbone
        self.in_proj = nn.Linear(1, d_model)
        self.time_embed = nn.Sequential(
            nn.Linear(2, d_model),
            nn.ReLU(),
            nn.Linear(d_model, d_model)
        )
        self.global_blocks = nn.ModuleList([
            STWaveFormerBlock(num_nodes=input_dim, d_model=d_model, nhead=nhead, dropout=dropout)
            for _ in range(num_layers)
        ])
        self.global_head = nn.Sequential(
            nn.Linear(d_model, d_model // 2),
            nn.ReLU(),
            nn.Linear(d_model // 2, 1)
        )
        
        # 3. Nhánh Cục bộ (Local Micro Branch): Multi-Scale Dilated TCN
        self.local_tcn = MultiScaleDilatedTCN(
            hidden_dim=d_model // 2,
            dropout=dropout
        )
        
        # 4. Bộ điều phối thích ứng (Contextual Meta-Gating)
        self.meta_gating = ContextualMetaGating(hidden_dim=32)

    def forward(self, x):
        # x: [batch, seq_len, input_dim] hoặc [batch, seq_len, input_dim, in_channels]
        time_feats = None
        if x.dim() == 4 and x.shape[-1] > 1:
            time_feats = x[:, :, :, 1:] # [B, T, N, 2]
            x_val = x[:, :, :, 0]       # [B, T, N]
        elif x.dim() == 4:
            x_val = x.squeeze(-1)
        else:
            x_val = x

        # B1. Chuẩn hóa chống trôi dạt phân phối RevIN
        x_norm = self.revin(x_val, mode='norm') # [B, T, N]
        
        # B2. Nhánh Toàn cục (Global Transformer Branch)
        h = self.in_proj(x_norm.unsqueeze(-1)) # [B, T, N, d_model]
        if time_feats is not None:
            t_emb = self.time_embed(time_feats)
            h = h + t_emb
            
        for block in self.global_blocks:
            h = block(h)
            
        h_last = h[:, -1, :, :] # [B, N, d_model]
        y_global = self.global_head(h_last).squeeze(-1) # [B, N]
        
        # B3. Nhánh Cục bộ (Local Dilated TCN Branch)
        y_local = self.local_tcn(x_norm) # [B, N]
        
        # B4. Tính cổng điều phối thích ứng (Meta-Gate)
        gate = self.meta_gating(x_val, time_feats) # [B, N] in [0, 1]
        
        # B5. Hòa trộn thông minh (Gated Blending)
        # Khi gate -> 1: Ưu tiên Global ST-WaveFormer (tối ưu cho Abilene & GEANT)
        # Khi gate -> 0: Ưu tiên Local Dilated TCN (lọc nhiễu gai xung trên SDN)
        y_fused = gate * y_global + (1.0 - gate) * y_local # [B, N]
        
        # B6. Giải chuẩn hóa khôi phục thang đo thực tế
        out = self.revin(y_fused, mode='denorm')
        return out
