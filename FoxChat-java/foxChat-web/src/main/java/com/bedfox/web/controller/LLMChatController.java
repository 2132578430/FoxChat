package com.bedfox.web.controller;

import com.bedfox.pojo.dto.AddLlmFriendDto;
import com.bedfox.pojo.dto.LlmFriendUpdateDto;
import com.bedfox.pojo.dto.LlmMsgHistoryReqDto;
import com.bedfox.service.service.LlmChatMsgService;
import com.bedfox.service.service.LlmChatService;
import com.bedfox.service.service.LlmUserService;
import com.bedfox.common.util.LoginUserHolder;
import com.bedfox.common.util.R;
import com.bedfox.pojo.vo.LlmChatMsgVo;
import com.bedfox.pojo.vo.LlmMsgHistoryVo;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;

import java.util.List;
import java.util.Map;

/**
 * @author bedFox
 * @date 2026/3/19 21:00
 */
@Slf4j
@RestController
@RequestMapping("/llm")
public class LLMChatController {

    @Resource
    LlmUserService llmUserService;

    @Resource
    LlmChatService llmChatService;

    @Resource
    LlmChatMsgService llmChatMsgService;

    @PostMapping("/chat")
    public R<LlmChatMsgVo> llmChat(@RequestBody Map<String, Object> requestMap) {
        String llmId = (String) requestMap.get("llmId");
        String msgContent = (String) requestMap.get("msgContent");
        String userId = LoginUserHolder.getUserId();

        LlmChatMsgVo chatMsgVo = llmChatService.llmChat(llmId, msgContent, userId);
        return R.ok(chatMsgVo);
    }

    @PostMapping("/superChat")
    public R<LlmChatMsgVo> llmSuperChat(@RequestBody Map<String, Object> requestMap) {
        String llmId = (String) requestMap.get("llmId");
        String msgContent = (String) requestMap.get("msgContent");
        String userId = LoginUserHolder.getUserId();

        LlmChatMsgVo chatMsgVo = llmChatService.llmSuperChat(llmId, msgContent, userId);
        return R.ok(chatMsgVo);
    }

    @PostMapping("/add")
    public R<String> addLlm(@RequestBody AddLlmFriendDto friendDto) {
        llmUserService.saveFriend(friendDto);
        return R.ok();
    }

    @PostMapping("/history")
    public R<List<LlmMsgHistoryVo>> getMsgHistory(@RequestBody LlmMsgHistoryReqDto reqDto) {
        List<LlmMsgHistoryVo> list = llmChatMsgService.getMsgHistory(
            LoginUserHolder.getUserId(),
            reqDto.getLlmId(),
            reqDto.getLastTime(),
            reqDto.getLastId()
        );

        return R.ok(list);
    }

    @PostMapping("/update")
    public R<Void> updateLlmFriend(@RequestBody LlmFriendUpdateDto updateDto) {
        llmUserService.updateFriend(updateDto);
        return R.ok();
    }

    /**
     * 模型头像上传接口
     */
    @PostMapping("/uploadAvatar")
    public R<String> uploadAvatar(@RequestParam("file") MultipartFile file) {
        String fileUrl = llmUserService.uploadAvatar(file);
        return R.ok(fileUrl);
    }
}
