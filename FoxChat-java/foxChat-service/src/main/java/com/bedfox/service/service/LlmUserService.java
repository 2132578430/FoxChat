package com.bedfox.service.service;

import com.baomidou.mybatisplus.extension.service.IService;
import com.bedfox.pojo.domain.LlmUser;
import com.bedfox.pojo.dto.AddLlmFriendDto;
import com.bedfox.pojo.vo.FriendVo;
import com.bedfox.pojo.vo.LlmChatMsgVo;
import com.bedfox.pojo.vo.LlmMsgHistoryVo;

import java.util.List;

/**
* @author 21325
* @description 针对表【llm_user】的数据库操作Service
* @createDate 2026-03-20 13:26:13
*/
public interface LlmUserService extends IService<LlmUser> {

    void saveFriend(AddLlmFriendDto friendDto);

    List<FriendVo> friendList(String userId);

    void deleteFriend(String friendId);

    LlmChatMsgVo llmChat(String llmId, String msgContent);

    List<LlmMsgHistoryVo> getMsgHistory(String llmId, Long lastTime);
}
