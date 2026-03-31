# 自动化测试脚本（PowerShell版本）

Write-Host "=== 开始执行测试 ==="

# 检查Java版本
Write-Host "Java版本："
java -version

# 检查Gradle版本
Write-Host "Gradle版本："
gradle -version

# 清理项目
Write-Host "清理项目..."
gradle clean

# 执行单元测试
Write-Host "执行单元测试..."
gradle test

# 检查测试结果
if ($LASTEXITCODE -ne 0) {
    Write-Host "测试失败！"
    exit 1
}

# 生成测试报告
Write-Host "生成测试报告..."
gradle jacocoTestReport

# 检查测试覆盖率
Write-Host "测试覆盖率："
$htmlContent = Get-Content -Path "build/reports/jacoco/test/html/index.html" -Raw
$coverage = [regex]::Match($htmlContent, '<span class="ctr2">([0-9.]+)</span>').Groups[1].Value
Write-Host $coverage

Write-Host "=== 测试完成 ==="
