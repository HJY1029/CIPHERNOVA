"""
模型评估器 - 评估代码生成质量和LLM性能
提供精确率、召回率、F1分数等评估指标
"""
import json
import asyncio
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from collections import defaultdict
from utils.logger import setup_logger
from agent.crypto_agent import CryptoAgent
from utils.code_validator import CodeValidator

logger = setup_logger()


class ModelEvaluator:
    """模型评估器 - 评估代码生成质量"""
    
    def __init__(self, config_path: str = "config.yaml"):
        """
        初始化评估器
        
        Args:
            config_path: 配置文件路径
        """
        self.config_path = config_path
        self.validator = CodeValidator()
        self.evaluation_results: List[Dict] = []
        _root = Path(__file__).resolve().parent.parent
        self.evaluation_dir = _root / "experiments" / "results" / "model_evaluator"
        self.evaluation_dir.mkdir(parents=True, exist_ok=True)
    
    async def evaluate_provider(
        self,
        provider: str,
        test_cases: List[Dict],
        enable_validation: bool = True
    ) -> Dict:
        """
        评估单个LLM提供商的性能
        
        Args:
            provider: LLM提供商名称
            test_cases: 测试用例列表，每个用例包含 algorithm, mode, language 等
            enable_validation: 是否启用代码验证
            
        Returns:
            评估结果字典
        """
        logger.info(f"开始评估LLM提供商: {provider}")
        
        agent = CryptoAgent(
            config_path=self.config_path,
            provider=provider,
            enable_validation=enable_validation
        )
        
        results = {
            'provider': provider,
            'total_cases': len(test_cases),
            'successful_generations': 0,  # 成功生成代码的数量
            'validation_passed': 0,  # 验证通过的数量
            'validation_failed': 0,  # 验证失败的数量
            'generation_failed': 0,  # 生成失败的数量
            'test_cases': []
        }
        
        for i, test_case in enumerate(test_cases, 1):
            logger.info(f"评估进度: {i}/{len(test_cases)} - {test_case}")
            
            case_result = {
                'test_case': test_case,
                'generation_success': False,
                'validation_success': False,
                'error_message': None,
                'code_length': 0,
                'generation_time': 0
            }
            
            try:
                # 生成代码
                start_time = datetime.now()
                code = await agent.generate_code(
                    algorithm=test_case['algorithm'],
                    mode=test_case.get('mode'),
                    operation=test_case.get('operation', '加密解密'),
                    language=test_case.get('language', 'python'),
                    **test_case.get('kwargs', {})
                )
                generation_time = (datetime.now() - start_time).total_seconds()
                
                case_result['generation_success'] = True
                case_result['code_length'] = len(code)
                case_result['generation_time'] = generation_time
                results['successful_generations'] += 1
                
                # 验证代码
                if enable_validation and code:
                    validation_success, validation_output = self.validator.validate(
                        code=code,
                        language=test_case.get('language', 'python')
                    )
                    case_result['validation_success'] = validation_success
                    
                    if validation_success:
                        results['validation_passed'] += 1
                    else:
                        results['validation_failed'] += 1
                        case_result['error_message'] = validation_output
                elif code:
                    # 如果没有启用验证，但代码生成成功，也算作验证通过
                    case_result['validation_success'] = True
                    results['validation_passed'] += 1
                
            except Exception as e:
                logger.error(f"评估测试用例失败: {e}")
                case_result['error_message'] = str(e)
                results['generation_failed'] += 1
            
            results['test_cases'].append(case_result)
        
        # 计算评估指标
        metrics = self._calculate_metrics(results)
        results['metrics'] = metrics
        
        logger.info(f"评估完成: {provider}")
        logger.info(f"  成功生成: {results['successful_generations']}/{results['total_cases']}")
        logger.info(f"  验证通过: {results['validation_passed']}/{results['total_cases']}")
        logger.info(f"  精确率: {metrics['precision']:.4f}")
        logger.info(f"  召回率: {metrics['recall']:.4f}")
        logger.info(f"  F1分数: {metrics['f1_score']:.4f}")
        
        return results
    
    def _calculate_metrics(self, results: Dict) -> Dict:
        """
        计算评估指标
        
        指标定义：
        - 精确率 (Precision): 验证通过的代码 / 成功生成的代码
        - 召回率 (Recall): 验证通过的代码 / 总测试用例数
        - F1分数: 2 * (精确率 * 召回率) / (精确率 + 召回率)
        - 准确率 (Accuracy): 验证通过的代码 / 总测试用例数
        
        Args:
            results: 评估结果
            
        Returns:
            指标字典
        """
        total = results['total_cases']
        successful = results['successful_generations']
        validated = results['validation_passed']
        
        # 精确率：验证通过的代码 / 成功生成的代码
        precision = validated / successful if successful > 0 else 0.0
        
        # 召回率：验证通过的代码 / 总测试用例数
        recall = validated / total if total > 0 else 0.0
        
        # F1分数
        f1_score = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0
        
        # 准确率：验证通过的代码 / 总测试用例数（与召回率相同）
        accuracy = recall
        
        # 生成成功率
        generation_rate = successful / total if total > 0 else 0.0
        
        return {
            'precision': precision,
            'recall': recall,
            'f1_score': f1_score,
            'accuracy': accuracy,
            'generation_rate': generation_rate
        }
    
    async def compare_providers(
        self,
        providers: List[str],
        test_cases: List[Dict],
        enable_validation: bool = True
    ) -> Dict:
        """
        对比多个LLM提供商的性能
        
        Args:
            providers: LLM提供商列表
            test_cases: 测试用例列表
            enable_validation: 是否启用代码验证
            
        Returns:
            对比结果字典
        """
        logger.info(f"开始对比评估: {providers}")
        
        comparison_results = {
            'test_cases': test_cases,
            'providers': {},
            'summary': {}
        }
        
        # 评估每个提供商
        for provider in providers:
            try:
                results = await self.evaluate_provider(
                    provider=provider,
                    test_cases=test_cases,
                    enable_validation=enable_validation
                )
                comparison_results['providers'][provider] = results
            except Exception as e:
                logger.error(f"评估提供商 {provider} 失败: {e}")
                comparison_results['providers'][provider] = {
                    'provider': provider,
                    'error': str(e)
                }
        
        # 生成对比摘要
        comparison_results['summary'] = self._generate_comparison_summary(
            comparison_results['providers']
        )
        
        return comparison_results
    
    def _generate_comparison_summary(self, provider_results: Dict[str, Dict]) -> Dict:
        """
        生成对比摘要
        
        Args:
            provider_results: 各提供商的评估结果
            
        Returns:
            对比摘要字典
        """
        summary = {
            'best_provider': None,
            'best_f1_score': 0.0,
            'metrics_comparison': {}
        }
        
        for provider, results in provider_results.items():
            if 'error' in results:
                continue
            
            metrics = results.get('metrics', {})
            f1_score = metrics.get('f1_score', 0.0)
            
            if f1_score > summary['best_f1_score']:
                summary['best_f1_score'] = f1_score
                summary['best_provider'] = provider
            
            summary['metrics_comparison'][provider] = {
                'precision': metrics.get('precision', 0.0),
                'recall': metrics.get('recall', 0.0),
                'f1_score': f1_score,
                'accuracy': metrics.get('accuracy', 0.0),
                'generation_rate': metrics.get('generation_rate', 0.0)
            }
        
        return summary
    
    def generate_report(
        self,
        evaluation_results: Dict,
        output_file: Optional[str] = None
    ) -> Path:
        """
        生成评估报告
        
        Args:
            evaluation_results: 评估结果
            output_file: 输出文件路径（可选）
            
        Returns:
            报告文件路径
        """
        if output_file is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_file = f"evaluation_report_{timestamp}.json"
        
        report_path = self.evaluation_dir / output_file
        
        # 添加报告元数据
        report = {
            'evaluation_date': datetime.now().isoformat(),
            'results': evaluation_results
        }
        
        # 保存JSON报告
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        
        logger.info(f"评估报告已保存: {report_path}")
        
        # 生成文本格式的分类报告
        text_report_path = report_path.with_suffix('.txt')
        self._generate_text_report(evaluation_results, text_report_path)
        
        return report_path
    
    def _generate_text_report(self, results: Dict, output_path: Path):
        """
        生成文本格式的分类报告
        
        Args:
            results: 评估结果
            output_path: 输出文件路径
        """
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write("=" * 80 + "\n")
            f.write("AI密码学代码生成助手 - 模型评估报告\n")
            f.write("=" * 80 + "\n\n")
            
            f.write(f"评估时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            
            # 如果是单提供商评估
            if 'provider' in results and 'metrics' in results:
                self._write_single_provider_report(f, results)
            # 如果是多提供商对比
            elif 'providers' in results:
                self._write_comparison_report(f, results)
    
    def _write_single_provider_report(self, f, results: Dict):
        """写入单提供商报告"""
        provider = results['provider']
        metrics = results['metrics']
        
        f.write(f"LLM提供商: {provider}\n")
        f.write(f"测试用例总数: {results['total_cases']}\n")
        f.write(f"成功生成: {results['successful_generations']}\n")
        f.write(f"验证通过: {results['validation_passed']}\n")
        f.write(f"验证失败: {results['validation_failed']}\n")
        f.write(f"生成失败: {results['generation_failed']}\n\n")
        
        f.write("-" * 80 + "\n")
        f.write("分类报告（Classification Report）\n")
        f.write("-" * 80 + "\n\n")
        
        f.write(f"精确率 (Precision): {metrics['precision']:.4f}\n")
        f.write(f"  说明: 在成功生成的代码中，验证通过的代码比例\n")
        f.write(f"  公式: 验证通过数 / 成功生成数\n")
        f.write(f"  计算: {results['validation_passed']} / {results['successful_generations']} = {metrics['precision']:.4f}\n\n")
        
        f.write(f"召回率 (Recall): {metrics['recall']:.4f}\n")
        f.write(f"  说明: 在所有测试用例中，验证通过的代码比例\n")
        f.write(f"  公式: 验证通过数 / 总测试用例数\n")
        f.write(f"  计算: {results['validation_passed']} / {results['total_cases']} = {metrics['recall']:.4f}\n\n")
        
        f.write(f"F1分数 (F1-Score): {metrics['f1_score']:.4f}\n")
        f.write(f"  说明: 精确率和召回率的调和平均数\n")
        f.write(f"  公式: 2 * (精确率 * 召回率) / (精确率 + 召回率)\n")
        f.write(f"  计算: 2 * ({metrics['precision']:.4f} * {metrics['recall']:.4f}) / ({metrics['precision']:.4f} + {metrics['recall']:.4f}) = {metrics['f1_score']:.4f}\n\n")
        
        f.write(f"准确率 (Accuracy): {metrics['accuracy']:.4f}\n")
        f.write(f"  说明: 验证通过的代码占总测试用例的比例\n")
        f.write(f"  公式: 验证通过数 / 总测试用例数\n")
        f.write(f"  计算: {results['validation_passed']} / {results['total_cases']} = {metrics['accuracy']:.4f}\n\n")
        
        f.write(f"生成成功率 (Generation Rate): {metrics['generation_rate']:.4f}\n")
        f.write(f"  说明: 成功生成代码的测试用例比例\n")
        f.write(f"  公式: 成功生成数 / 总测试用例数\n")
        f.write(f"  计算: {results['successful_generations']} / {results['total_cases']} = {metrics['generation_rate']:.4f}\n\n")
    
    def _write_comparison_report(self, f, results: Dict):
        """写入多提供商对比报告"""
        f.write("多LLM提供商性能对比评估\n\n")
        
        # 写入摘要
        summary = results.get('summary', {})
        if summary.get('best_provider'):
            f.write(f"最佳提供商: {summary['best_provider']} (F1分数: {summary['best_f1_score']:.4f})\n\n")
        
        # 写入各提供商的详细报告
        for provider, provider_results in results['providers'].items():
            if 'error' in provider_results:
                f.write(f"\n{provider}: 评估失败 - {provider_results['error']}\n")
                continue
            
            f.write("\n" + "=" * 80 + "\n")
            f.write(f"提供商: {provider}\n")
            f.write("=" * 80 + "\n\n")
            
            metrics = provider_results.get('metrics', {})
            f.write(f"精确率 (Precision): {metrics.get('precision', 0.0):.4f}\n")
            f.write(f"召回率 (Recall): {metrics.get('recall', 0.0):.4f}\n")
            f.write(f"F1分数 (F1-Score): {metrics.get('f1_score', 0.0):.4f}\n")
            f.write(f"准确率 (Accuracy): {metrics.get('accuracy', 0.0):.4f}\n")
            f.write(f"生成成功率: {metrics.get('generation_rate', 0.0):.4f}\n")
            f.write(f"验证通过数: {provider_results.get('validation_passed', 0)} / {provider_results.get('total_cases', 0)}\n")
        
        # 写入对比表格
        f.write("\n" + "=" * 80 + "\n")
        f.write("性能对比表\n")
        f.write("=" * 80 + "\n\n")
        f.write(f"{'提供商':<15} {'精确率':<10} {'召回率':<10} {'F1分数':<10} {'准确率':<10} {'生成成功率':<10}\n")
        f.write("-" * 80 + "\n")
        
        for provider, metrics in summary.get('metrics_comparison', {}).items():
            f.write(f"{provider:<15} "
                   f"{metrics['precision']:<10.4f} "
                   f"{metrics['recall']:<10.4f} "
                   f"{metrics['f1_score']:<10.4f} "
                   f"{metrics['accuracy']:<10.4f} "
                   f"{metrics['generation_rate']:<10.4f}\n")
    
    def get_default_test_cases(self) -> List[Dict]:
        """
        获取默认测试用例
        
        Returns:
            测试用例列表
        """
        test_cases = [
            # Python测试用例
            {'algorithm': 'AES', 'mode': 'CBC', 'language': 'python'},
            {'algorithm': 'AES', 'mode': 'GCM', 'language': 'python'},
            {'algorithm': 'DES', 'mode': 'CBC', 'language': 'python'},
            {'algorithm': 'RSA', 'language': 'python', 'operation': '加密解密'},
            {'algorithm': 'SM4', 'mode': 'CBC', 'language': 'python'},
            
            # C测试用例
            {'algorithm': 'AES', 'mode': 'CBC', 'language': 'c'},
            {'algorithm': 'DES', 'mode': 'CBC', 'language': 'c'},
            {'algorithm': 'RSA', 'language': 'c', 'operation': '加密解密'},
            
            # C++测试用例
            {'algorithm': 'AES', 'mode': 'CBC', 'language': 'cpp'},
            {'algorithm': 'RSA', 'language': 'cpp', 'operation': '加密解密'},
        ]
        return test_cases

