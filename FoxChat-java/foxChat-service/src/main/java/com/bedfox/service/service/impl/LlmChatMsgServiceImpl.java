package com.bedfox.service.service.impl;

import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.bedfox.pojo.domain.LlmChatMsg;
import com.bedfox.service.mapper.LlmChatMsgMapper;
import com.bedfox.service.service.LlmChatMsgService;
import jakarta.annotation.Resource;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.util.List;

/**
* @author 21325
* @description 针对表【llm_chat_msg】的数据库操作Service实现
* @createDate 2026-03-25 09:46:16
*/
@Service
public class LlmChatMsgServiceImpl extends ServiceImpl<LlmChatMsgMapper, LlmChatMsg>
    implements LlmChatMsgService{

    @Resource
    LlmChatMsgMapper llmChatMsgMapper;

    @Override
    public List<LlmChatMsg> getMsgHistory(String userId, String llmId, LocalDateTime lastTime, Long lastId) {
        return llmChatMsgMapper.getMsgHistory(userId, llmId, lastTime, lastId);
    }
}




