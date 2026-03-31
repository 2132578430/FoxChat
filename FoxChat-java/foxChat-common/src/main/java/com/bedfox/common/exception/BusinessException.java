package com.bedfox.common.exception;

import com.bedfox.common.constant.ResultStatusConstant;

public class BusinessException extends RuntimeException {
    private final ResultStatusConstant status;
    public BusinessException(ResultStatusConstant status) {
        super(status.getMsg());
        this.status = status;
    }

    public ResultStatusConstant getStatus() {
        return status;
    }
}
