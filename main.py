import asyncio
import sys
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.table import Table
from agent.crypto_agent import CryptoAgent
from utils.logger import setup_logger
from utils.config_loader import ConfigLoader
from utils.code_tester import CodeTester
import os

console = Console()
logger = setup_logger()

def show_menu():
    """显示主菜单"""
    console.print("\n" + "="*60, style="bold cyan")
    console.print(Panel.fit(
        "[bold yellow] 密码学代码生成助手[/bold yellow]\n"
        "使用AI辅助生成密码学代码（支持Python、C、C++）",
        border_style="cyan"
    ))
    console.print("="*60 + "\n", style="bold cyan")
    
    table = Table(title="支持的算法", show_header=True, header_style="bold magenta")
    table.add_column("算法", style="cyan", no_wrap=True)
    table.add_column("模式/功能", style="green")
    
    table.add_row("DES", "ECB, CBC, CFB, OFB")
    table.add_row("AES", "ECB, CBC, CFB, OFB, GCM, CTR")
    table.add_row("RSA", "密钥生成、加密、解密、签名、验证")
    table.add_row("SM4", "ECB, CBC, CFB, OFB")
    
    console.print(table)
    console.print()

def get_algorithm_choice():
    """获取算法选择"""
    algorithms = ['DES', 'AES', 'RSA', 'SM4']
    console.print("\n请选择算法：")
    for i, alg in enumerate(algorithms, 1):
        console.print(f"  {i}. {alg}")
    
    choice = Prompt.ask("\n请输入选项", choices=['1', '2', '3', '4'], default='1')
    return algorithms[int(choice) - 1]

def get_mode_choice(algorithm: str):
    """获取模式选择"""
    modes_map = {
        'DES': ['ECB', 'CBC', 'CFB', 'OFB'],
        'AES': ['ECB', 'CBC', 'CFB', 'OFB', 'GCM', 'CTR'],
        'SM4': ['ECB', 'CBC', 'CFB', 'OFB']
    }
    
    if algorithm == 'RSA':
        return None
    
    modes = modes_map.get(algorithm, [])
    if not modes:
        return None
    
    console.print(f"\n请选择{algorithm}模式：")
    for i, mode in enumerate(modes, 1):
        console.print(f"  {i}. {mode}")
    
    choice = Prompt.ask("\n请输入选项", choices=[str(i) for i in range(1, len(modes)+1)], default='1')
    return modes[int(choice) - 1]

def get_language_choice(agent: CryptoAgent):
    """获取编程语言选择"""
    languages = agent.list_supported_languages()
    lang_names = {
        'python': 'Python',
        'c': 'C',
        'cpp': 'C++',
        'c++': 'C++'
    }
    
    console.print("\n请选择编程语言：")
    for i, lang in enumerate(languages, 1):
        lang_display = lang_names.get(lang, lang.capitalize())
        console.print(f"  {i}. {lang_display}")
    
    choice = Prompt.ask("\n请输入选项", choices=[str(i) for i in range(1, len(languages)+1)], default='1')
    return languages[int(choice) - 1]

def get_provider_choice() -> str:
    """获取LLM提供商选择"""
    # 临时加载配置以获取提供商列表
    config = ConfigLoader()
    llm_providers = config._config.get('llm_providers', {})
    default_provider = config.get('default_provider', 'deepseek')
    
    from utils.llm_provider_ui import llm_provider_display_name, llm_provider_key_ready
    from agent.llm.base import get_api_key

    # 获取已启用的提供商
    enabled_providers = []
    for provider, config_data in llm_providers.items():
        if config_data.get('enabled', False):
            enabled_providers.append(provider)
    
    if not enabled_providers:
        console.print("[bold red]错误: 没有已启用的LLM提供商！[/bold red]")
        console.print("请在config.yaml中启用至少一个LLM提供商。")
        sys.exit(1)
    
    console.print("\n请选择LLM提供商：")
    for i, provider in enumerate(enabled_providers, 1):
        provider_display = llm_provider_display_name(provider)
        has_key = llm_provider_key_ready(llm_providers[provider], get_api_key)
        status = "[green]✓[/green]" if has_key else "[yellow]⚠[/yellow] (未配置API密钥)"
        
        # 标记默认提供商
        default_mark = " [默认]" if provider == default_provider else ""
        console.print(f"  {i}. {provider_display}{default_mark} {status}")
    
    choice = Prompt.ask(
        "\n请输入选项", 
        choices=[str(i) for i in range(1, len(enabled_providers)+1)], 
        default=str(enabled_providers.index(default_provider) + 1) if default_provider in enabled_providers else '1'
    )
    
    selected_provider = enabled_providers[int(choice) - 1]
    
    # 检查API密钥
    api_key_env = llm_providers[selected_provider].get('api_key_env', '')
    if api_key_env and not os.getenv(api_key_env):
        console.print(f"[yellow]警告: {selected_provider} 的API密钥未配置（环境变量 {api_key_env}）[/yellow]")
        if not Confirm.ask("是否继续？", default=False):
            return get_provider_choice()  # 重新选择
    
    return selected_provider

async def generate_code_workflow(agent: CryptoAgent, enable_validation: bool):
    """代码生成工作流"""
    # 获取用户选择
    algorithm = get_algorithm_choice()
    mode = get_mode_choice(algorithm)
    language = get_language_choice(agent)
    
    # 获取额外要求
    console.print("\n[dim]（可选）请输入额外要求，直接回车跳过：[/dim]")
    requirements = Prompt.ask("额外要求", default="")
    
    kwargs = {}
    if requirements:
        kwargs['额外要求'] = requirements
    
    # 生成代码
    with console.status("[bold yellow]正在生成代码..."):
        filepath, validation_result = await agent.generate_and_save(
            algorithm=algorithm,
            mode=mode,
            language=language,
            validate=enable_validation,
            **kwargs
        )
    
    console.print(f"\n[bold green]✓ 代码生成成功！[/bold green]")
    console.print(f"[cyan]文件路径:[/cyan] {filepath}")
    
    # 显示验证结果
    if validation_result:
        success, output = validation_result
        if success:
            console.print(f"[bold green]✓ 代码验证通过！[/bold green]")
            if output:
                console.print(f"[dim]验证输出:[/dim]\n{output}")
        else:
            console.print(f"[bold yellow]⚠ 代码验证失败[/bold yellow]")
            console.print(f"[dim]错误信息:[/dim]\n{output}")
            console.print("[yellow]提示: 代码已生成，但验证失败。请检查代码或编译器是否已安装。[/yellow]")
    
    # 读取代码
    with open(filepath, 'r', encoding='utf-8') as f:
        code = f.read()
    
    # 询问是否查看代码
    if Confirm.ask("\n是否查看生成的代码？"):
        console.print(Panel(code, title="生成的代码", border_style="green"))
    
    # 询问是否进行自定义测试
    if Confirm.ask("\n是否进行自定义测试？", default=False):
        await custom_test_workflow(filepath, language, code)
    
    return filepath, language, code

async def custom_test_workflow(filepath: Path, language: str, code: str):
    """自定义测试工作流"""
    console.print("\n[bold cyan]自定义测试[/bold cyan]")
    console.print("你可以提供明文和预期密文（测试加密），或密文和预期明文（测试解密）\n")
    
    # 选择测试类型
    console.print("1. 测试加密（输入明文和预期密文）")
    console.print("2. 测试解密（输入密文和预期明文）")
    test_type = Prompt.ask(
        "请选择测试类型",
        choices=['1', '2'],
        default='1'
    )
    
    tester = CodeTester()
    
    if test_type == '1':
        # 测试加密
        console.print("\n[bold]测试加密[/bold]")
        plaintext = Prompt.ask("请输入明文")
        expected_ciphertext = Prompt.ask("请输入预期密文")
        
        with console.status("[bold yellow]正在测试..."):
            success, message, details = tester.test(
                code=code,
                language=language,
                plaintext=plaintext,
                expected_ciphertext=expected_ciphertext
            )
        
        # 显示测试结果
        console.print()
        if success:
            console.print("[bold green]✓ 测试成功！[/bold green]")
        else:
            console.print("[bold red]✗ 测试失败[/bold red]")
        
        # 显示详细对比
        if details and details.get('actual') and details.get('expected'):
            console.print("\n[cyan]结果对比：[/cyan]")
            console.print(f"  实际结果: [yellow]{details['actual']}[/yellow]")
            console.print(f"  预期结果: [yellow]{details['expected']}[/yellow]")
            
            if details.get('actual_normalized') and details.get('expected_normalized'):
                console.print("\n[cyan]规范化后对比：[/cyan]")
                console.print(f"  实际结果: [yellow]{details['actual_normalized']}[/yellow]")
                console.print(f"  预期结果: [yellow]{details['expected_normalized']}[/yellow]")
        
        # 显示完整消息
        console.print(f"\n[dim]{message}[/dim]")
    
    else:
        # 测试解密
        console.print("\n[bold]测试解密[/bold]")
        ciphertext = Prompt.ask("请输入密文")
        expected_plaintext = Prompt.ask("请输入预期明文")
        
        with console.status("[bold yellow]正在测试..."):
            success, message, details = tester.test(
                code=code,
                language=language,
                ciphertext=ciphertext,
                expected_plaintext=expected_plaintext
            )
        
        # 显示测试结果
        console.print()
        if success:
            console.print("[bold green]✓ 测试成功！[/bold green]")
        else:
            console.print("[bold red]✗ 测试失败[/bold red]")
        
        # 显示详细对比
        if details and details.get('actual') and details.get('expected'):
            console.print("\n[cyan]结果对比：[/cyan]")
            console.print(f"  实际结果: [yellow]{details['actual']}[/yellow]")
            console.print(f"  预期结果: [yellow]{details['expected']}[/yellow]")
            
            if details.get('actual_normalized') and details.get('expected_normalized'):
                console.print("\n[cyan]规范化后对比：[/cyan]")
                console.print(f"  实际结果: [yellow]{details['actual_normalized']}[/yellow]")
                console.print(f"  预期结果: [yellow]{details['expected_normalized']}[/yellow]")
        
        # 显示完整消息
        console.print(f"\n[dim]{message}[/dim]")

async def main():
    """主函数"""
    try:
        show_menu()
        
        # 选择LLM提供商
        provider = get_provider_choice()
        
        # 初始化Agent
        enable_validation = Confirm.ask("\n是否启用代码验证？", default=True)
        agent = CryptoAgent(enable_validation=enable_validation, provider=provider)
        
        from utils.llm_provider_ui import llm_provider_display_name
        provider_display = llm_provider_display_name(provider)
        console.print(f"[green]✓[/green] 已连接到LLM: {provider_display} ({provider})")
        
        # 测试API连接
        with console.status("[bold yellow]正在测试API连接..."):
            success, message = await agent.test_connection()
        
        if success:
            console.print(f"[bold green]✓ {message}[/bold green]")
        else:
            console.print(f"[bold red]✗ {message}[/bold red]")
            console.print("[yellow]API连接测试失败，无法使用此LLM提供商。[/yellow]")
            if Confirm.ask("\n是否重新选择LLM提供商？", default=True):
                await main()  # 重新开始，包括选择提供商
                return
            else:
                console.print("[yellow]已取消操作[/yellow]")
                return
        
        # 生成代码
        filepath, language, code = await generate_code_workflow(agent, enable_validation)
            
        # 询问是否继续
        while Confirm.ask("\n是否继续生成其他代码？"):
            # 询问是否更换LLM提供商
            if Confirm.ask("是否更换LLM提供商？", default=False):
                await main()  # 重新开始，包括选择提供商
                return
            else:
                # 继续使用当前提供商生成代码
                filepath, language, code = await generate_code_workflow(agent, enable_validation)
            
        console.print("\n[bold cyan]感谢使用！再见！[/bold cyan]")
    
    except KeyboardInterrupt:
        console.print("\n\n[yellow]已取消操作[/yellow]")
        sys.exit(0)
    except Exception as e:
        logger.error(f"发生错误: {e}")
        console.print(f"\n[bold red]错误:[/bold red] {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
