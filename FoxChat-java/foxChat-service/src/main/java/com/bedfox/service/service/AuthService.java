package com.bedfox.service.service;

import com.bedfox.pojo.dto.RegisterDto;
import com.bedfox.pojo.dto.UserDto;
import com.bedfox.pojo.vo.UserInfo;

/**
 * @author bedFox
 */
public interface AuthService {
    UserInfo login(UserDto userDto);

    void register(RegisterDto registerDto);

    void sendCode(String email);
}
