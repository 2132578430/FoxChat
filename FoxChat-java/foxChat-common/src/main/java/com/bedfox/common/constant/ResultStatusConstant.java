package com.bedfox.common.constant;

import lombok.Getter;

/**
 * @author bedFox
 * 10 - 统一接口服务
 *  00 - 成功
 *  01 - 失败
 * 110 - 登录业务错误
 *  00 - 账号或密码不符合格式
 *  01 - 账户不存在
 *  02 - 密码错误
 *  03 - 验证码错误
 * 120 - 注册业务错误
 *  00 - 账号或密码不符合格式
 *  01 - 验证码错误
 *  02 - 验证码过于频繁
 * 130 - 好友业务错误
 *  00 - 好友不存在
 * 140 - 文件储存业务错误
 *  00 - 文件上传失败
 *  01 - 文件上传重复
 * 200 - 内网请求错误
 *  00 - 内网信息发送缺失
 *
 */
@Getter
public enum ResultStatusConstant {
    // 统一服务状态
    SUCCESS(1000, "响应成功"),
    UNKNOWN_ERROR(1001, "未知错误"),

    // 登录服务状态异常
    LOGIN_FORMAT_ERROR_EXCEPTION(11000, "登录账号或密码不符合格式"),
    LOGIN_ACCOUNT_NOT_EXIST_EXCEPTION(11001, "账户不存在"),
    LOGIN_PASSWORD_ERROR_EXCEPTION(11002, "密码错误"),
    LOGIN_CODE_ERROR_EXCEPTION(11003, "验证码错误"),

    // 注册服务状态异常
    REGISTER_FORMAT_ERROR_EXCEPTION(12000, "登录账号或密码不符合格式"),
    REGISTER_CODE_ERROR_EXCEPTION(12001,"验证码错误"),
    REGISTER_CODE_REPEAT_EXCEPTION(12002, "请勿重复发送验证码"),
    REGISTER_USER_REPEAT_EXCEPTION(12003, "邮箱或用户名重复"),

    // 好友服务状态异常
    FRIEND_NO_EXIST_EXCEPTION(13000, "好友不存在"),

    // 文件上传状态异常
    FILE_UPLOAD_ERROR_EXCEPTION(14000, "文件上传失败"),

    // 内网工作异常
    RAG_MSG_ERROR_EXCEPTION(20000, "rag数据库内网消息传输错误");

    private Integer code;
    private String msg;

    ResultStatusConstant(Integer code, String msg) {
        this.code = code;
        this.msg = msg;
    }

    ResultStatusConstant() {
    }
}
