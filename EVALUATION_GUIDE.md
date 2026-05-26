# 模型评估指南

## 概述

模型评估模块用于评估不同LLM提供商生成代码的质量，生成包含精确率、召回率、F1分数等标准分类指标的详细报告。

## 快速开始

### 1. 单提供商评估

```python
import asyncio
from utils.model_evaluator import ModelEvaluator

async def main():
    evaluator = ModelEvaluator()
    
    # 定义测试用例
    test_cases = [
        {'algorithm': 'AES', 'mode': 'CBC', 'language': 'python'},
        {'algorithm': 'DES', 'mode': 'CBC', 'language': 'python'},
        {'algorithm': 'RSA', 'language': 'python', 'operation': '加密解密'},
    ]
    
    # 评估提供商
    results = await evaluator.evaluate_provider(
        provider='deepseek',
        test_cases=test_cases,
        enable_validation=True
    )
    
    # 生成报告
    report_path = evaluator.generate_report(results)
    print(f"评估报告已保存: {report_path}")
    print(f"文本报告: {report_path.with_suffix('.txt')}")

asyncio.run(main())
```

### 2. 多提供商对比

```python
import asyncio
from utils.model_evaluator import ModelEvaluator

async def main():
    evaluator = ModelEvaluator()
    
    # 使用默认测试用例
    test_cases = evaluator.get_default_test_cases()
    
    # 对比多个提供商
    providers = ['deepseek', 'openai']
    
    comparison_results = await evaluator.compare_providers(
        providers=providers,
        test_cases=test_cases,
        enable_validation=True
    )
    
    # 生成对比报告
    report_path = evaluator.generate_report(comparison_results)
    
    # 查看最佳提供商
    summary = comparison_results['summary']
    print(f"最佳提供商: {summary['best_provider']}")
    print(f"最佳F1分数: {summary['best_f1_score']:.4f}")

asyncio.run(main())
```

## 评估指标说明

### 精确率 (Precision)

**定义**：在成功生成的代码中，验证通过的代码比例

**公式**：
```
精确率 = 验证通过的代码数 / 成功生成的代码数
```

**意义**：衡量生成代码的质量。精确率越高，说明生成的代码越可靠。

**示例**：
- 生成了10个代码，其中8个验证通过
- 精确率 = 8/10 = 0.8 (80%)

### 召回率 (Recall)

**定义**：在所有测试用例中，验证通过的代码比例

**公式**：
```
召回率 = 验证通过的代码数 / 总测试用例数
```

**意义**：衡量代码生成的覆盖率。召回率越高，说明能成功处理更多测试用例。

**示例**：
- 有20个测试用例，其中15个验证通过
- 召回率 = 15/20 = 0.75 (75%)

### F1分数 (F1-Score)

**定义**：精确率和召回率的调和平均数

**公式**：
```
F1分数 = 2 * (精确率 * 召回率) / (精确率 + 召回率)
```

**意义**：综合评估指标，平衡精确率和召回率。F1分数越高，说明模型整体性能越好。

**示例**：
- 精确率 = 0.8，召回率 = 0.75
- F1分数 = 2 * (0.8 * 0.75) / (0.8 + 0.75) = 0.774

### 准确率 (Accuracy)

**定义**：验证通过的代码占总测试用例的比例

**公式**：
```
准确率 = 验证通过的代码数 / 总测试用例数
```

**说明**：在本项目中，准确率与召回率相同。

### 生成成功率 (Generation Rate)

**定义**：成功生成代码的测试用例占总测试用例的比例

**公式**：
```
生成成功率 = 成功生成的代码数 / 总测试用例数
```

**意义**：衡量代码生成功能的可用性。

## 评估报告

评估完成后会生成两种格式的报告：

### JSON格式报告

包含完整的评估数据，便于程序处理：

```json
{
  "evaluation_date": "2024-01-01T12:00:00",
  "results": {
    "provider": "deepseek",
    "total_cases": 10,
    "successful_generations": 9,
    "validation_passed": 8,
    "validation_failed": 1,
    "generation_failed": 1,
    "metrics": {
      "precision": 0.8889,
      "recall": 0.8000,
      "f1_score": 0.8421,
      "accuracy": 0.8000,
      "generation_rate": 0.9000
    }
  }
}
```

### 文本格式报告

人类可读的分类报告，包含详细的指标说明和计算公式：

```
================================================================================
AI密码学代码生成助手 - 模型评估报告
================================================================================

评估时间: 2024-01-01 12:00:00

LLM提供商: deepseek
测试用例总数: 10
成功生成: 9
验证通过: 8
验证失败: 1
生成失败: 1

--------------------------------------------------------------------------------
分类报告（Classification Report）
--------------------------------------------------------------------------------

精确率 (Precision): 0.8889
  说明: 在成功生成的代码中，验证通过的代码比例
  公式: 验证通过数 / 成功生成数
  计算: 8 / 9 = 0.8889

召回率 (Recall): 0.8000
  说明: 在所有测试用例中，验证通过的代码比例
  公式: 验证通过数 / 总测试用例数
  计算: 8 / 10 = 0.8000

F1分数 (F1-Score): 0.8421
  说明: 精确率和召回率的调和平均数
  公式: 2 * (精确率 * 召回率) / (精确率 + 召回率)
  计算: 2 * (0.8889 * 0.8000) / (0.8889 + 0.8000) = 0.8421

准确率 (Accuracy): 0.8000
  说明: 验证通过的代码占总测试用例的比例
  公式: 验证通过数 / 总测试用例数
  计算: 8 / 10 = 0.8000

生成成功率 (Generation Rate): 0.9000
  说明: 成功生成代码的测试用例比例
  公式: 成功生成数 / 总测试用例数
  计算: 9 / 10 = 0.9000
```

## 运行示例

运行评估示例：

```bash
python examples/evaluation_example.py
```

## 评估最佳实践

1. **测试用例设计**：
   - 覆盖不同的算法（AES、DES、RSA、SM4）
   - 覆盖不同的模式（ECB、CBC、GCM等）
   - 覆盖不同的编程语言（Python、C、C++）
   - 包含边界情况和常见错误场景

2. **评估频率**：
   - 定期评估（如每周一次）
   - 在更换LLM提供商时评估
   - 在更新提示词后评估

3. **结果分析**：
   - 关注F1分数，综合评估性能
   - 如果精确率低，说明生成的代码质量有问题
   - 如果召回率低，说明生成成功率有问题
   - 对比不同提供商的性能，选择最适合的

4. **持续改进**：
   - 根据评估结果优化提示词
   - 根据评估结果调整验证策略
   - 根据评估结果选择最佳LLM提供商

## 注意事项

1. **API密钥**：确保已配置相应LLM提供商的API密钥
2. **代码验证**：C/C++代码验证需要安装gcc/g++编译器
3. **评估时间**：评估过程可能需要较长时间，取决于测试用例数量和LLM API响应速度
4. **API费用**：评估会调用LLM API，可能产生费用

## 文件位置

- **评估模块**：`utils/model_evaluator.py`
- **评估示例**：`examples/evaluation_example.py`
- **评估结果**：保存在 `evaluation_results/` 目录

