# HTTPS门户网站系统设计方案

## 一、项目概述

### 1.1 项目背景
搭建一个HTTPS门户网站系统，作为多个子系统的统一入口。用户登录门户后，可以无缝访问各个子系统，实现SSO单点登录。

### 1.2 核心需求
- **统一认证**：门户登录后，子系统自动登录
- **独立访问**：每个子系统可以独立从外网访问
- **HTTPS通讯**：前后端通讯使用HTTPS
- **中等规模**：5-20个子系统

### 1.3 技术栈
- **Nginx**：反向代理，SSL证书终止
- **Spring Boot**：门户服务和子系统服务
- **Redis**：Token缓存，Session管理
- **MySQL**：用户数据，子系统注册表

---

## 二、整体架构

### 2.1 架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                          用户浏览器                              │
└────────┬─────────────────┬─────────────────┬────────────────────┘
         │ HTTPS           │ HTTPS           │ HTTPS
         ▼                 ▼                 ▼
┌─────────────┐   ┌─────────────┐   ┌─────────────┐
│ Nginx       │   │ Nginx       │   │ Nginx       │
│ (portal)    │   │ (app1)      │   │ (app2)      │
│ SSL终止     │   │ SSL终止     │   │ SSL终止     │
└──────┬──────┘   └──────┬──────┘   └──────┬──────┘
       │ HTTP          │ HTTP          │ HTTP
       ▼               ▼               ▼
┌─────────────┐   ┌─────────────┐   ┌─────────────┐
│ 门户服务     │   │ 子系统A     │   │ 子系统B     │
│ :8080       │   │ :8081       │   │ :8082       │
└─────────────┘   └─────────────┘   └─────────────┘
       │                 │                 │
       └─────────────────┴─────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│                      共享数据层                                  │
│  ┌──────────────────────────┐  ┌──────────────────────────┐   │
│  │        MySQL             │  │        Redis             │   │
│  │                          │  │                          │   │
│  │ - users表（共享）         │  │ - token缓存              │   │
│  │ - subsystems表（注册表）  │  │ - session数据            │   │
│  │ - permissions表          │  │ - 限流计数               │   │
│  └──────────────────────────┘  └──────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 架构说明

**关键设计点**：
1. **Nginx作为SSL终止点**：每个服务前都有Nginx，处理HTTPS加解密
2. **内网HTTP通讯**：Nginx到Spring Boot使用HTTP，简化配置
3. **共享用户表**：所有服务使用同一个users表
4. **独立域名**：每个子系统有独立域名（如app1.yourdomain.com）

---

## 三、HTTPS证书方案

### 3.1 证书类型
使用**泛域名证书** `*.yourdomain.com`，一个证书覆盖所有子域名。

### 3.2 证书申请方式

**方案1：Let's Encrypt（免费）**
- 支持泛域名证书
- 自动续期（90天有效期）
- 使用工具：certbot、acme.sh

**方案2：阿里云/腾讯云（付费）**
- 购买泛域名证书
- 有效期1年或2年
- 提供证书文件下载

### 3.3 证书部署
每个服务的Nginx都部署同一份证书文件：
- `fullchain.pem`：证书链
- `privkey.pem`：私钥

---

## 四、SSO认证流程

### 4.1 认证流程图

```
┌──────────┐         ┌──────────┐         ┌──────────┐         ┌──────────┐
│  用户    │         │  门户    │         │  Redis   │         │ 子系统A  │
│  浏览器  │         │  服务    │         │          │         │          │
└─────┬────┘         └─────┬────┘         └─────┬────┘         └─────┬────┘
      │                    │                    │                    │
      │ 1. 登录成功        │                    │                    │
      │──────────────────>│                    │                    │
      │                    │                    │                    │
      │                    │ 2. 生成长期token   │                    │
      │                    │   存入Redis        │                    │
      │                    │──────────────────>│                    │
      │                    │                    │                    │
      │ 3. 返回门户首页    │                    │                    │
      │<──────────────────│                    │                    │
      │   Set-Cookie: portal_token=xxx         │                    │
      │                    │                    │                    │
      │ 4. 点击"子系统A"   │                    │                    │
      │──────────────────>│                    │                    │
      │                    │                    │                    │
      │                    │ 5. 验证portal_token │                    │
      │                    │──────────────────>│                    │
      │                    │<──────────────────│                    │
      │                    │                    │                    │
      │                    │ 6. 生成临时token    │                    │
      │                    │   存入Redis(30秒)   │                    │
      │                    │──────────────────>│                    │
      │                    │                    │                    │
      │ 7. 302重定向       │                    │                    │
      │<──────────────────│                    │                    │
      │   Location: https://app1.yourdomain.com/sso/callback?      │
      │            token=temp_token&redirect=/dashboard            │
      │                    │                    │                    │
      │ 8. 访问子系统A     │                    │                    │
      │─────────────────────────────────────────────────────────>│
      │                    │                    │                    │
      │                    │                    │ 9. 验证临时token   │
      │                    │                    │<───────────────────│
      │                    │                    │                    │
      │                    │                    │ 10. 返回用户信息    │
      │                    │                    │───────────────────>│
      │                    │                    │                    │
      │                    │                    │ 11. 删除临时token   │
      │                    │                    │<───────────────────│
      │                    │                    │                    │
      │                    │                    │ 12. 生成子系统token │
      │                    │                    │<───────────────────│
      │                    │                    │                    │
      │ 13. 重定向到目标页面                    │                    │
      │<─────────────────────────────────────────────────────────│
      │   Location: /dashboard                │                    │
      │   Set-Cookie: app1_token=zzz           │                    │
```

### 4.2 Token类型说明

**长期Token（portal_token）**：
- 用户登录门户后生成
- 有效期7天
- 存储在Redis和浏览器Cookie

**临时Token（temp_token）**：
- 点击子系统时生成
- 有效期30秒
- 一次性使用，验证后删除
- 用于安全传递用户信息

**子系统Token（app1_token）**：
- 子系统验证临时token后生成
- 有效期7天
- 存储在Redis和浏览器Cookie

### 4.3 安全措施

1. **临时Token防止泄露**：
   - URL中的token有效期只有30秒
   - 验证后立即删除
   - 防止重放攻击

2. **Token绑定信息**：
   - 可选：绑定IP地址
   - 可选：绑定User-Agent

---

## 五、数据库设计

### 5.1 MySQL表结构

#### users表（共享用户表）

| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | BIGINT | 主键，用户ID |
| username | VARCHAR(50) | 用户名，唯一 |
| password | VARCHAR(255) | 密码（加密存储） |
| email | VARCHAR(100) | 邮箱，唯一 |
| phone | VARCHAR(20) | 手机号，唯一 |
| nickname | VARCHAR(50) | 昵称/显示名称 |
| avatar | VARCHAR(255) | 头像URL |
| status | TINYINT | 状态：0-禁用，1-正常 |
| create_time | DATETIME | 创建时间 |
| update_time | DATETIME | 更新时间 |
| last_login_time | DATETIME | 最后登录时间 |
| last_login_ip | VARCHAR(50) | 最后登录IP |

#### subsystems表（子系统注册表）

| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | BIGINT | 主键，子系统ID |
| name | VARCHAR(50) | 子系统名称（如"订单管理"） |
| code | VARCHAR(50) | 子系统代码（如"order-system"） |
| url | VARCHAR(255) | 子系统访问URL（如 https://app1.yourdomain.com） |
| internal_url | VARCHAR(255) | 内网通讯URL（可选，如 http://192.168.1.100:8081） |
| icon | VARCHAR(255) | 子系统图标URL |
| description | VARCHAR(500) | 子系统描述 |
| status | TINYINT | 状态：0-禁用，1-正常 |
| sort_order | INT | 排序顺序 |
| create_time | DATETIME | 注册时间 |
| update_time | DATETIME | 更新时间 |

#### user_permissions表（用户权限表）

| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | BIGINT | 主键 |
| user_id | BIGINT | 用户ID |
| subsystem_id | BIGINT | 子系统ID |
| permission_type | VARCHAR(20) | 权限类型：access/admin |
| create_time | DATETIME | 授权时间 |

**索引**：UNIQUE(user_id, subsystem_id) 防止重复授权

#### subsystem_secrets表（子系统密钥表）

| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | BIGINT | 主键 |
| subsystem_id | BIGINT | 子系统ID |
| api_key | VARCHAR(100) | API密钥 |
| api_secret | VARCHAR(255) | API密钥密文 |
| expire_time | DATETIME | 密钥过期时间 |
| status | TINYINT | 状态：0-失效，1-有效 |
| create_time | DATETIME | 创建时间 |

#### login_logs表（登录日志表）

| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | BIGINT | 主键 |
| user_id | BIGINT | 用户ID |
| login_type | VARCHAR(20) | 登录类型：portal/sso |
| subsystem_id | BIGINT | 子系统ID（SSO登录时） |
| login_ip | VARCHAR(50) | 登录IP |
| login_time | DATETIME | 登录时间 |
| login_result | TINYINT | 登录结果：0-失败，1-成功 |
| fail_reason | VARCHAR(200) | 失败原因 |

### 5.2 Redis存储结构

#### Key设计

| Key格式 | 说明 | TTL |
|---------|------|-----|
| sso:portal:{token} | 门户长期Token | 7天 |
| sso:temp:{token} | 临时Token | 30秒 |
| sso:{appCode}:{token} | 子系统Token | 7天 |
| session:{userId} | 用户Session | 30分钟 |
| rate:login:{ip} | 限流计数 | 1分钟 |

#### Value结构（JSON）

```json
{
  "userId": 123,
  "username": "zhangsan",
  "email": "zhangsan@example.com",
  "permissions": ["app1", "app2", "app3"],
  "createTime": 1712345678,
  "expireTime": 1712349278
}
```

### 5.3 数据库连接方案

**推荐方案**：共享MySQL实例

```
MySQL服务器
├── Database: portal_db
│   └── Tables: users, subsystems, user_permissions, ...
├── Database: app1_db
│   └── Tables: orders, products, ...
├── Database: app2_db
│   └── Tables: documents, files, ...
```

**说明**：
- 所有服务连接同一个MySQL实例
- users表放在portal_db，所有服务都可以访问
- 子系统业务表放在各自的Database

---

## 六、子系统注册机制

### 6.1 注册流程

```
┌──────────┐         ┌──────────┐         ┌──────────┐
│ 子系统   │         │  门户    │         │  MySQL   │
│ 管理员   │         │  服务    │         │          │
└─────┬────┘         └─────┬────┘         └─────┬────┘
      │                    │                    │
      │ 1. 登录门户管理后台 │                    │
      │──────────────────>│                    │
      │                    │                    │
      │ 2. 填写子系统信息   │                    │
      │   - 名称           │                    │
      │   - 代码           │                    │
      │   - URL            │                    │
      │   - 描述           │                    │
      │──────────────────>│                    │
      │                    │                    │
      │                    │ 3. 存入subsystems表 │
      │                    │──────────────────>│
      │                    │                    │
      │                    │ 4. 生成API密钥      │
      │                    │──────────────────>│
      │                    │   存入subsystem_secrets表
      │                    │                    │
      │ 5. 返回注册成功     │                    │
      │<──────────────────│                    │
      │   返回：           │                    │
      │   - subsystem_id   │                    │
      │   - api_key        │                    │
      │   - api_secret     │                    │
      │                    │                    │
      │ 6. 子系统配置密钥   │                    │
      │   用于验证token     │                    │
```

### 6.2 注册信息

子系统注册时需要提供：
- **名称**：显示给用户的名称（如"订单管理系统"）
- **代码**：系统内部标识（如"order-system"）
- **URL**：外网访问地址（如 https://app1.yourdomain.com）
- **内网URL**：可选，内网通讯地址
- **图标**：子系统图标URL
- **描述**：子系统功能描述

### 6.3 API密钥机制

门户为每个子系统生成API密钥：
- **api_key**：公开的密钥标识
- **api_secret**：加密的密钥内容

**用途**：
- 子系统验证临时token时，可以使用API密钥向门户请求用户信息
- 或者直接访问共享Redis验证token

---

## 七、HTTPS配置步骤

### 7.1 申请证书

**使用Let's Encrypt（推荐）**：

```bash
# 安装certbot
sudo apt install certbot

# 申请泛域名证书
sudo certbot certonly --manual --preferred-challenges dns -d "*.yourdomain.com" -d "yourdomain.com"

# 按提示添加DNS TXT记录验证域名所有权
# 验证成功后，证书文件保存在 /etc/letsencrypt/live/yourdomain.com/
```

**证书文件**：
- `fullchain.pem`：证书链
- `privkey.pem`：私钥

### 7.2 Nginx配置

**门户服务Nginx配置**：

```nginx
# /etc/nginx/sites-available/portal.conf

server {
    listen 443 ssl;
    server_name portal.yourdomain.com;

    # SSL证书配置
    ssl_certificate /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;

    # SSL优化配置
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;

    # 反向代理到门户服务
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

# HTTP重定向到HTTPS
server {
    listen 80;
    server_name portal.yourdomain.com;
    return 301 https://$server_name$request_uri;
}
```

**子系统Nginx配置**：

```nginx
# /etc/nginx/sites-available/app1.conf

server {
    listen 443 ssl;
    server_name app1.yourdomain.com;

    # 使用同一份泛域名证书
    ssl_certificate /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;

    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    location / {
        proxy_pass http://127.0.0.1:8081;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

server {
    listen 80;
    server_name app1.yourdomain.com;
    return 301 https://$server_name$request_uri;
}
```

### 7.3 证书自动续期

```bash
# 测试续期
sudo certbot renew --dry-run

# 设置自动续期（cron）
sudo crontab -e

# 添加以下行（每天检查一次）
0 0 * * * /usr/bin/certbot renew --quiet --post-hook "systemctl reload nginx"
```

### 7.4 HTTPS与HTTP的区别

**HTTPS通讯流程**：

```
客户端                    Nginx                    Spring Boot
  │                        │                        │
  │ 1. HTTPS请求           │                        │
  │   (加密数据)           │                        │
  │──────────────────────>│                        │
  │                        │                        │
  │                        │ 2. SSL解密             │
  │                        │                        │
  │                        │ 3. HTTP请求            │
  │                        │   (明文数据)           │
  │                        │──────────────────────>│
  │                        │                        │
  │                        │                        │ 4. 处理请求
  │                        │                        │
  │                        │ 5. HTTP响应            │
  │                        │   (明文数据)           │
  │                        │<──────────────────────│
  │                        │                        │
  │                        │ 6. SSL加密             │
  │                        │                        │
  │ 7. HTTPS响应           │                        │
  │   (加密数据)           │                        │
  │<──────────────────────│                        │
```

**关键区别**：
1. **SSL/TLS层**：HTTPS在HTTP基础上增加SSL/TLS加密层
2. **证书验证**：客户端验证服务器证书
3. **数据加密**：传输数据加密，防止窃听
4. **端口不同**：HTTP用80端口，HTTPS用443端口

**对开发的影响**：
- Spring Boot服务内部仍用HTTP（内网安全）
- Nginx处理SSL加解密（性能优化）
- 开发时无需关心SSL细节

---

## 八、实现步骤建议

### 8.1 第一阶段：基础搭建

1. **申请泛域名证书**
   - 使用Let's Encrypt申请 `*.yourdomain.com` 证书

2. **搭建门户服务**
   - 创建Spring Boot项目
   - 实现用户登录/注册
   - 实现Token生成和验证
   - 实现子系统注册管理

3. **配置门户Nginx**
   - 配置HTTPS
   - 配置反向代理

4. **搭建Redis和MySQL**
   - 配置Redis连接
   - 创建数据库和表

### 8.2 第二阶段：SSO实现

1. **实现门户重定向逻辑**
   - 点击子系统时生成临时token
   - 重定向到子系统

2. **实现子系统SSO回调**
   - 接收临时token
   - 验证token
   - 生成子系统token
   - 自动登录

### 8.3 第三阶段：子系统接入

1. **创建子系统服务**
   - 复用门户的用户表
   - 实现业务功能

2. **配置子系统Nginx**
   - 使用同一份证书
   - 配置HTTPS

3. **注册子系统到门户**
   - 在门户管理后台注册
   - 配置API密钥

---

## 九、类似开源项目参考

### 9.1 SSO相关项目

**CAS（Central Authentication Service）**：
- 官网：https://apereo.github.io/cas/
- 特点：成熟的SSO解决方案
- 适用：大型企业系统

**OAuth2.0 + OIDC**：
- Spring Security OAuth2
- Keycloak（开源身份管理）
- 适用：标准化认证

### 9.2 门户网站参考

**Portainer**：
- 官网：https://www.portainer.io/
- 特点：容器管理门户
- 参考：多服务统一入口设计

**Nginx Proxy Manager**：
- 官网：https://nginxproxymanager.com/
- 特点：Nginx可视化管理
- 参考：SSL证书管理界面

---

## 十、注意事项

### 10.1 安全注意事项

1. **Token安全**：
   - 临时token有效期30秒
   - 验证后立即删除
   - 防止重放攻击

2. **HTTPS配置**：
   - 使用TLSv1.2或TLSv1.3
   - 禁用弱加密算法
   - 定期更新证书

3. **数据库安全**：
   - 密码加密存储（BCrypt）
   - API密钥加密存储
   - 定期备份

### 10.2 性能优化

1. **Nginx优化**：
   - 启用SSL Session Cache
   - 启用HTTP/2
   - 启用Gzip压缩

2. **Redis优化**：
   - 合理设置TTL
   - 使用连接池
   - 监控内存使用

3. **数据库优化**：
   - 合理设计索引
   - 使用连接池
   - 定期优化表

---

## 十一、总结

本设计方案采用**Nginx + Spring Boot + Redis + MySQL**架构，实现HTTPS门户网站和SSO单点登录。

**核心特点**：
- 使用泛域名证书简化HTTPS配置
- 临时token机制保证安全
- 共享用户表实现统一认证
- 独立域名满足独立访问需求

**下一步**：
- 根据本方案创建实现计划
- 按阶段逐步实现功能