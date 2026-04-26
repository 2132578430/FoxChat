# 以下为项目过程中遇到的问题

## 当与模型聊天的时候
由于使用的是HTTP请求，那么就会一直需要等待模型返回信息，无法上下看

## 记忆过大
由于没有好的记忆压缩方法，因此目前最大只能支持50轮对话，大了以后token就会指数级增长

## 记忆初始化处理有问题
记忆初始化出来只包含state，[memory_event_extractor.md](app/core/prompts/memory_event_extractor.md)
此提示词中的事件完全没有提取出来
