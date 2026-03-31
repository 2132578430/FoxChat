#!/bin/bash

# 自动化测试脚本

echo "=== 开始执行测试 ==="

# 检查Java版本
echo "Java版本："
java -version

# 检查Gradle版本
echo "Gradle版本："
gradle -version

# 清理项目
echo "清理项目..."
gradle clean

# 执行单元测试
echo "执行单元测试..."
gradle test

# 检查测试结果
if [ $? -ne 0 ]; then
    echo "测试失败！"
    exit 1
fi

# 生成测试报告
echo "生成测试报告..."
gradle jacocoTestReport

# 检查测试覆盖率
echo "测试覆盖率："
cat build/reports/jacoco/test/html/index.html | grep -oP '(?<=<span class="ctr2">)[0-9.]+(?=</span>)' | head -1

echo "=== 测试完成 ==="
