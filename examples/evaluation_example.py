"""
模型评估示例 - 演示如何使用ModelEvaluator进行代码生成质量评估
"""
import asyncio
from utils.model_evaluator import ModelEvaluator


async def example_single_provider_evaluation():
    """单提供商评估示例"""
    print("=" * 80)
    print("示例1: 单LLM提供商评估")
    print("=" * 80)
    
    evaluator = ModelEvaluator()
    
    # 定义测试用例
    test_cases = [
        {'algorithm': 'AES', 'mode': 'CBC', 'language': 'python'},
        {'algorithm': 'AES', 'mode': 'GCM', 'language': 'python'},
        {'algorithm': 'DES', 'mode': 'CBC', 'language': 'python'},
        {'algorithm': 'RSA', 'language': 'python', 'operation': '加密解密'},
    ]
    
    # 评估DeepSeek提供商
    results = await evaluator.evaluate_provider(
        provider='deepseek',
        test_cases=test_cases,
        enable_validation=True
    )
    
    # 生成报告
    report_path = evaluator.generate_report(results)
    print(f"\n评估报告已保存: {report_path}")
    print(f"文本报告: {report_path.with_suffix('.txt')}")


async def example_multi_provider_comparison():
    """多提供商对比评估示例"""
    print("\n" + "=" * 80)
    print("示例2: 多LLM提供商性能对比")
    print("=" * 80)
    
    evaluator = ModelEvaluator()
    
    # 使用默认测试用例
    test_cases = evaluator.get_default_test_cases()
    
    # 对比多个提供商
    providers = ['deepseek', 'openai']  # 可以根据配置添加更多提供商
    
    comparison_results = await evaluator.compare_providers(
        providers=providers,
        test_cases=test_cases,
        enable_validation=True
    )
    
    # 生成对比报告
    report_path = evaluator.generate_report(comparison_results)
    print(f"\n对比评估报告已保存: {report_path}")
    print(f"文本报告: {report_path.with_suffix('.txt')}")
    
    # 打印摘要
    summary = comparison_results.get('summary', {})
    print("\n评估摘要:")
    print(f"最佳提供商: {summary.get('best_provider', 'N/A')}")
    print(f"最佳F1分数: {summary.get('best_f1_score', 0.0):.4f}")
    
    print("\n各提供商性能对比:")
    for provider, metrics in summary.get('metrics_comparison', {}).items():
        print(f"\n{provider}:")
        print(f"  精确率: {metrics['precision']:.4f}")
        print(f"  召回率: {metrics['recall']:.4f}")
        print(f"  F1分数: {metrics['f1_score']:.4f}")
        print(f"  准确率: {metrics['accuracy']:.4f}")


async def example_custom_test_cases():
    """自定义测试用例评估示例"""
    print("\n" + "=" * 80)
    print("示例3: 自定义测试用例评估")
    print("=" * 80)
    
    evaluator = ModelEvaluator()
    
    # 自定义测试用例（可以包含额外要求）
    custom_test_cases = [
        {
            'algorithm': 'AES',
            'mode': 'CBC',
            'language': 'python',
            'kwargs': {'额外要求': '使用pycryptodome库'}
        },
        {
            'algorithm': 'RSA',
            'language': 'python',
            'operation': '签名',
            'kwargs': {'额外要求': '使用cryptography库'}
        },
    ]
    
    results = await evaluator.evaluate_provider(
        provider='deepseek',
        test_cases=custom_test_cases,
        enable_validation=True
    )
    
    # 打印结果
    print(f"\n评估结果:")
    print(f"总测试用例: {results['total_cases']}")
    print(f"成功生成: {results['successful_generations']}")
    print(f"验证通过: {results['validation_passed']}")
    
    metrics = results['metrics']
    print(f"\n评估指标:")
    print(f"精确率: {metrics['precision']:.4f}")
    print(f"召回率: {metrics['recall']:.4f}")
    print(f"F1分数: {metrics['f1_score']:.4f}")
    print(f"准确率: {metrics['accuracy']:.4f}")


async def main():
    """主函数"""
    try:
        # 运行示例1：单提供商评估
        await example_single_provider_evaluation()
        
        # 运行示例2：多提供商对比（需要配置多个API密钥）
        # await example_multi_provider_comparison()
        
        # 运行示例3：自定义测试用例
        # await example_custom_test_cases()
        
    except Exception as e:
        print(f"评估过程中发生错误: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())

