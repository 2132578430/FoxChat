package com.bedfox.web.controller;

import com.bedfox.pojo.dto.AddLlmFriendDto;
import com.bedfox.pojo.dto.LlmMsgHistoryReqDto;
import com.bedfox.service.service.LlmUserService;
import com.bedfox.common.util.R;
import com.bedfox.pojo.vo.LlmChatMsgVo;
import com.bedfox.pojo.vo.LlmMsgHistoryVo;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.web.bind.annotation.*;

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

    @PostMapping("/chat")
    public R<LlmChatMsgVo> llmChat(@RequestBody Map<String, Object> requestMap) {
        String llmId = (String) requestMap.get("llmId");
        String msgContent = (String) requestMap.get("msgContent");

        LlmChatMsgVo chatMsgVo = llmUserService.llmChat(llmId, msgContent);
        return R.ok(chatMsgVo);
    }

    @PostMapping("/superChat")
    public R<LlmChatMsgVo> llmSuperChat(@RequestBody Map<String, Object> requestMap) {
        String llmId = (String) requestMap.get("llmId");
        String msgContent = (String) requestMap.get("msgContent");

        LlmChatMsgVo chatMsgVo = llmUserService.llmSuperChat(llmId, msgContent);
        return R.ok(chatMsgVo);
    }

    @PostMapping("/add")
    public R<String> addLlm(@RequestBody AddLlmFriendDto friendDto) {
        llmUserService.saveFriend(friendDto);
        return R.ok();
    }

    @PostMapping("/history")
    public R<List<LlmMsgHistoryVo>> getMsgHistory(@RequestBody LlmMsgHistoryReqDto reqDto) {
        List<LlmMsgHistoryVo> list = llmUserService.getMsgHistory(
            reqDto.getLlmId(),
            reqDto.getLastTime(),
            reqDto.getLastId()
        );

        return R.ok(list);
    }

    @PostMapping("/update")
    public R<Void> updateLlmFriend(@RequestBody Map<String, Object> requestMap) {
        String llmId = (String) requestMap.get("llmId");
        String nickname = (String) requestMap.get("nickname");
        String faceImage = (String) requestMap.get("faceImage");

        llmUserService.updateFriend(llmId, nickname, faceImage);
        return R.ok();
    }
}
