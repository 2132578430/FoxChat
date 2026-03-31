# 测试使用说明

## 测试目录结构

本项目的测试目录结构如下：

```
foxChat/
├── foxChat-common/src/test/          # 公共模块测试
├── foxChat-netty/src/test/           # Netty模块测试
├── foxChat-pojo/src/test/            # 数据模型测试
├── foxChat-service/src/test/         # 服务层测试
├── foxChat-web/src/test/             # Web层测试
├── run-tests.sh                      # Linux/Mac测试脚本
└── run-tests.ps1                     # Windows测试脚本
```

## 测试类型

本项目包含以下类型的测试：

1. **单元测试**：测试服务层的核心功能，如用户认证、用户管理等
2. **集成测试**：测试控制器的API接口，如登录、注册、获取用户信息等
3. **端到端测试**：测试完整的业务流程，如注册→登录→发送消息→发送好友请求

## 运行测试

### 在Linux/Mac上运行

1. 确保项目已编译：
   ```bash
   ./gradlew build
   ```

2. 运行自动化测试脚本：
   ```bash
   chmod +x run-tests.sh
   ./run-tests.sh
   ```

### 在Windows上运行

1. 确保项目已编译：
   ```powershell
   .\gradlew build
   ```

2. 运行自动化测试脚本：
   ```powershell
   .\run-tests.ps1
   ```

### 直接使用Gradle运行

1. 运行所有测试：
   ```bash
   ./gradlew test
   ```

2. 运行特定模块的测试：
   ```bash
   ./gradlew :foxChat-service:test
   ```

3. 运行特定测试类：
   ```bash
   ./gradlew test --tests "com.bedfox.service.service.impl.AuthServiceImplTest"
   ```

## 查看测试报告

测试完成后，测试报告将生成在以下位置：

1. **测试结果**：`build/test-results/test/`
2. **代码覆盖率报告**：`build/reports/jacoco/test/html/`

打开 `build/reports/jacoco/test/html/index.html` 文件可以查看详细的代码覆盖率报告。

## 测试覆盖范围

本项目的测试覆盖了以下核心功能：

1. **用户认证**：注册、登录
2. **用户管理**：获取用户信息、更新用户信息、删除用户
3. **消息功能**：发送消息、获取历史消息
4. **好友功能**：发送好友请求、接受好友请求、获取好友列表
5. **群组功能**：创建群组、加入群组、发送群组消息

## 注意事项

1. 运行测试前确保数据库、Redis、RabbitMQ等依赖服务已启动
2. 测试过程中会模拟各种场景，包括成功和失败的情况
3. 测试完成后会自动生成测试报告，可用于分析测试覆盖率和测试结果

## 自定义测试

如果需要添加新的测试用例，可以按照以下步骤进行：

1. 在对应模块的 `src/test/java` 目录下创建测试类
2. 使用 `@Test` 注解标记测试方法
3. 使用 `Mockito` 模拟依赖项
4. 运行测试验证功能

## 常见问题

1. **测试失败**：检查依赖服务是否启动，测试数据是否正确
2. **测试覆盖率低**：添加更多测试用例，覆盖更多代码路径
3. **测试运行缓慢**：优化测试代码，减少不必要的依赖

---

通过运行测试，可以确保项目的核心功能正常工作，提高代码质量和稳定性。
