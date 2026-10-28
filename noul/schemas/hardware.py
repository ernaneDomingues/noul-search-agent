"""Hardware feasibility contracts (Phase 3: #09 inference footprint estimator)."""
from typing import List, Optional

from noul.schemas._compat import BaseModel, Field


class PrecisionMemory(BaseModel):
    weights_gb: float
    kv_cache_8k_context_gb: float
    recommended_min_vram_gb: float


class HardwareFeasibility(BaseModel):
    paper_id: str
    total_parameters_billion: float
    active_parameters_billion: Optional[float] = None
    detected_model_sizes_b: List[float] = Field(default_factory=list)
    architecture_type: str = "dense"  # "dense" | "moe" | "encoder_only" | "rnn_ssm"
    fp16: PrecisionMemory
    int8: PrecisionMemory
    int4: PrecisionMemory
    reported_training_hardware: Optional[str] = None
    operational_tier: str = "workstation"  # "edge_consumer_gpu" | "workstation" | "datacenter_cluster"
    notes: List[str] = Field(default_factory=list)
