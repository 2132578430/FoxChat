package com.bedfox;

import org.mybatis.spring.annotation.MapperScan;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.cloud.openfeign.EnableFeignClients;
import org.springframework.scheduling.annotation.EnableScheduling;

/**
 * @author bedFox
 */
@SpringBootApplication
@EnableScheduling
@EnableFeignClients
@MapperScan("com.bedfox.service.mapper")
public class BedfoxChatApplication {

    public static void main(String[] args) {
        SpringApplication.run(BedfoxChatApplication.class, args);
    }

}
