"""识屏层（开源版）：具体的截图识别实现不包含在开源仓库里（它依赖游戏素材）。

这里只保留把「识别器的原始输出」打包成契约 Observation 的通用接口：observe.observation() 和适配器注册表。
接入自己的识别器时，用 observe.register_adapter(scene, fn) 注册每个页面的字段转换。
"""
