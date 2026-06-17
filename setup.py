from setuptools import setup, find_packages

setup(
    name="runcore",
    version="0.1.0",
    description="Agent trace optimization and benchmarking toolkit",
    author="RunCore",
    python_requires=">=3.10",
    packages=find_packages(),
    install_requires=[
        "typer>=0.9.0",
        "rich>=13.0.0",
        "anthropic>=0.25.0",
        "tiktoken>=0.6.0",
        "pydantic>=2.0.0",
        "jinja2>=3.1.0",
        "pytest>=7.0.0",
    ],
    entry_points={
        "console_scripts": [
            "runcore=runcore.cli.main:app",
        ],
    },
)
