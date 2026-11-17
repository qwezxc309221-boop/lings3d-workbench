"""限制历史缓存并扩展长序列位置编码。"""
def configure_long_session(model, total):
    from lingbot_map.layers.rope import WanRotaryPosEmbed
    required=max(1024,total+16)
    for module in model.modules():
        rope=getattr(module,'rope3d',None)
        if isinstance(rope,WanRotaryPosEmbed) and rope.max_seq_len<required:
            module.rope3d=WanRotaryPosEmbed(rope.attention_head_dim,rope.patch_size,
                max_seq_len=required,theta=10000.,fhw_dim=rope.fhw_dim)


def cap_special_cache(model, limit):
    def trim(cache):
        if isinstance(cache,list):
            for item in cache:trim(item)
        elif isinstance(cache,dict):
            for name,value in list(cache.items()):
                if name.endswith('_special') and value is not None and value.shape[2]>limit:
                    cache[name]=value[:,:, -limit:].clone()
    trim(getattr(model.aggregator,'kv_cache',None))
    trim(getattr(model.camera_head,'kv_cache',None))
    # 相机缓存每关键帧一个 token，需单独裁剪并保留初始化帧。
    camera=getattr(model.camera_head,'kv_cache',None)
    for cache in camera or []:
        for name,value in list(cache.items()):
            if (name.startswith(('k_','v_')) and not name.endswith('_special') and value is not None
                    and value.ndim==5 and value.shape[2]>limit+2):
                import torch
                cache[name]=torch.cat([value[:,:,:2],value[:,:,-limit:]],dim=2)
