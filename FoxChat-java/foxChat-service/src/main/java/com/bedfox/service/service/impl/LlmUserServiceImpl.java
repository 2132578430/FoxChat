package com.bedfox.service.service.impl;

import com.alibaba.fastjson2.JSON;
import com.alibaba.fastjson2.TypeReference;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.extension.service.impl.ServiceImpl;
import com.bedfox.common.constant.ChatRoleConstant;
import com.bedfox.common.constant.FileContstant;
import com.bedfox.common.util.*;
import com.bedfox.pojo.domain.LlmChatMsg;
import com.bedfox.pojo.domain.LlmUser;
import com.bedfox.pojo.dto.AddLlmFriendDto;
import com.bedfox.pojo.dto.LlmFriendUpdateDto;
import com.bedfox.service.mapper.LlmUserMapper;
import com.bedfox.service.remote.ChatClient;
import com.bedfox.service.service.LlmChatMsgService;
import com.bedfox.service.service.LlmUserService;
import com.bedfox.pojo.to.ChatMqMsgTo;
import com.bedfox.pojo.to.ChatMsgTo;
import com.bedfox.pojo.vo.FriendVo;
import com.bedfox.pojo.vo.LlmChatMsgVo;
import com.bedfox.pojo.vo.LlmMsgHistoryVo;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.BeanUtils;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.util.List;

import org.springframework.web.multipart.MultipartFile;


/**
* @author 21325
* @description 针对表【llm_user】的数据库操作Service实现
* @createDate 2026-03-20 13:26:13
*/
@Slf4j
@Service
public class LlmUserServiceImpl extends ServiceImpl<LlmUserMapper, LlmUser>
    implements LlmUserService{

    @Resource
    MqUtil mqUtil;

    @Resource
    MinioUtil minioUtil;

    @Resource
    ChatClient chatClient;

    @Resource
    LlmChatMsgService llmChatMsgService;

    /**
     * 保存大模型
     * @param friendDto
     */
    @Override
    public void saveFriend(AddLlmFriendDto friendDto) {
        String userId = LoginUserHolder.getUserId();
        LlmUser llm = new LlmUser();
        String myName = friendDto.getMyName();
        String partnerName = friendDto.getPartnerName();
        String experience = friendDto.getExperience();

        // 拼接主语信息，降低模型识别错主人的概率
        String nicknamePrompt = "如果这个名字已经是爱称或昵称（如宝贝、小可爱等），则无需生成简称；如果像王大锤这样是正式名字，则生成1-3个简短的昵称供你使用。";
        String fullExperience = "以下为你与我的经历。你应该称呼我为" + myName + "，你的名字是" + partnerName + "。你与我的经历：" + experience + ". 要求：" + nicknamePrompt;

        llm.setLlmName(friendDto.getNickname());
        llm.setMemoryContent(fullExperience);
        llm.setUserId(userId);

        // 保存模型
        save(llm);

        // 将记忆存入rabbitmq，记忆初始化交给python
        ChatMqMsgTo chatMqMsgTo = new ChatMqMsgTo();

        chatMqMsgTo.setUserId(userId);
        chatMqMsgTo.setExperience(fullExperience);
        chatMqMsgTo.setLlmId(llm.getId());

        mqUtil.sendChatMsg(chatMqMsgTo);

        System.out.println(chatMqMsgTo);
    }

    /**
     * 返回用户模型列表给好友检索
     * @return
     */
    @Override
    public List<FriendVo> friendList(String userId) {
        List<LlmUser> llmList = list(new LambdaQueryWrapper<LlmUser>()
                .eq(LlmUser::getUserId, userId));

        return llmList.stream()
                .map(llmUser -> {
                    FriendVo friendVo = new FriendVo();

                    friendVo.setRole(ChatRoleConstant.LLM);
                    friendVo.setOnline(Boolean.TRUE);
                    friendVo.setUsername(llmUser.getLlmName());
                    friendVo.setUserId(llmUser.getId());
                    friendVo.setNickname(llmUser.getLlmName());
                    friendVo.setFaceImage(llmUser.getFaceImage());

                    return friendVo;
                })
                .toList();
    }

    /**
     * 删除llm好友
     * @param llmId
     */
    @Override
    public void deleteFriend(String llmId) {
        String userId = LoginUserHolder.getUserId();

        // 先删除向量库相关信息
        chatClient.deleteMsg(userId, llmId);

        // 删除数据库相关信息
        removeById(llmId);

        llmChatMsgService.remove(new LambdaQueryWrapper<LlmChatMsg>()
                .eq(LlmChatMsg::getLlmId, llmId).eq(LlmChatMsg::getSendUserId, userId));
    }

    /**
     * 更新llm好友信息
     * @param updateDto
     */
    @Override
    public void updateFriend(LlmFriendUpdateDto updateDto) {
        String userId = LoginUserHolder.getUserId();
        LlmUser llmUser = getById(updateDto.getLlmId());
        if (llmUser == null || !llmUser.getUserId().equals(userId)) {
            return;
        }
        llmUser.setLlmName(updateDto.getNickname());
        llmUser.setFaceImage(updateDto.getFaceImage());
        updateById(llmUser);
    }

    /**
     * 聊天主lu
     * @param llmId
     * @param msgContent
     * @return
     */
    @Override
    public LlmChatMsgVo llmChat(String llmId, String msgContent) {
        String userId = LoginUserHolder.getUserId();

        // 保存用户消息
        LlmChatMsg llmChatMsgHuman = buildLlmChatMsg(msgContent, llmId, userId, true);
        llmChatMsgService.save(llmChatMsgHuman);

        ChatMsgTo chatMsg = new ChatMsgTo();
        chatMsg.setLlmId(llmId);
        chatMsg.setMsgContent(msgContent);
        chatMsg.setUserId(userId);

        String resultJson = chatClient.chatMsg(chatMsg);

        log.info("接收到消息：{}", resultJson);

        resultJson = resultJson.replaceAll("</?[a-zA-Z_]+>", "");

        M<String> msg = JSON.parseObject(resultJson, new TypeReference<M<String>>() {});
        String data = msg.getData();

        // 保存模型消息
        LlmChatMsg llmChatMsgAi = buildLlmChatMsg(data, llmId, userId, false);
        llmChatMsgService.save(llmChatMsgAi);

        LlmChatMsgVo chatMsgVo = new LlmChatMsgVo();

        chatMsgVo.setMsg(data);

        return chatMsgVo;
    }

    /**
     * 导演模式聊天
     * @param llmId
     * @param msgContent
     * @return
     */
    @Override
    public LlmChatMsgVo llmSuperChat(String llmId, String msgContent) {
        String userId = LoginUserHolder.getUserId();

        log.info("【导演模式】聊天请求：userId={}, llmId={}, msgContent={}", userId, llmId, msgContent);

        // 保存用户消息
        LlmChatMsg llmChatMsgHuman = buildLlmChatMsg(msgContent, llmId, userId, true);
        llmChatMsgService.save(llmChatMsgHuman);
        log.debug("【导演模式】用户消息已保存到数据库");

        // 构建请求对象
        ChatMsgTo chatMsg = new ChatMsgTo();
        chatMsg.setLlmId(llmId);
        chatMsg.setMsgContent(msgContent);
        chatMsg.setUserId(userId);

        // 调用导演模式专用接口
        log.info("【导演模式】开始调用 Python superChatMsg 接口...");
        String resultJson = chatClient.superChatMsg(chatMsg);
        log.info("【导演模式】收到 Python 响应：{}", resultJson);

        // 解析响应
        M<String> msg = JSON.parseObject(resultJson, new TypeReference<M<String>>() {});
        String data = msg.getData();

        if (data == null || data.isEmpty()) {
            log.warn("【导演模式】Python 返回数据为空");
            data = "抱歉，导演模式暂时无法回应...";
        }

        // 保存模型消息
        LlmChatMsg llmChatMsgAi = buildLlmChatMsg(data, llmId, userId, false);
        llmChatMsgService.save(llmChatMsgAi);
        log.debug("【导演模式】AI 回复已保存到数据库");

        LlmChatMsgVo chatMsgVo = new LlmChatMsgVo();
        chatMsgVo.setMsg(data);

        log.info("【导演模式】聊天完成：userId={}, llmId={}", userId, llmId);
        return chatMsgVo;
    }

    private LlmChatMsg buildLlmChatMsg(String msgContent, String llmId, String userId, Boolean isHuman) {
        LlmChatMsg chatMsg = new LlmChatMsg();

        chatMsg.setMsgContent(msgContent);
        chatMsg.setLlmId(llmId);
        chatMsg.setSendUserId(userId);
        chatMsg.setIsHuman(isHuman);
        chatMsg.setCreateTime(LocalDateTime.now());

        return chatMsg;
    }

    /**
     * 获取llm聊天历史记录
     *
     * @return
     */
    @Override
    public List<LlmMsgHistoryVo> getMsgHistory(String llmId, Long lastTime, Long lastId) {
        String userId = LoginUserHolder.getUserId();
        LocalDateTime currentTime = TimeUtil.timestampToLdt(lastTime);

        // 获取与模型得的聊天消息
        List<LlmChatMsg> llmChatMsgList = llmChatMsgService.getMsgHistory(userId, llmId, currentTime, lastId);

        return llmChatMsgList.stream()
                .map(llmChatMsg -> {
                    LlmMsgHistoryVo chatMsgVo = new LlmMsgHistoryVo();
                    BeanUtils.copyProperties(llmChatMsg, chatMsgVo);

                    return chatMsgVo;
                })
                .toList();
    }

    /**
     * 上传模型头像
     * @param file 文件
     * @return 文件URL
     */
    @Override
    public String uploadAvatar(MultipartFile file) {
        String userId = LoginUserHolder.getUserId();
        return minioUtil.uploadFile(file, FileContstant.LLM_AVATAR_BIZPATH, userId);
    }
}