import os
import pathlib
from typing import Any, Dict

import setuptools  # type: ignore

here = pathlib.Path(__file__).parent.resolve()
about: Dict[Any, Any] = {}
with open(os.path.join(here, "version.py"), "r") as f:
    exec(f.read(), about)

setuptools.setup(
    name="meshagent-computers",
    version=about["__version__"],
    description="Computer Building Blocks for Meshagent",
    long_description=(here / "README.md").read_text(encoding="utf-8"),
    long_description_content_type="text/markdown",
    url="",
    classifiers=[       
    ],
    keywords=[],
    license="Apache License 2.0",
    packages=setuptools.find_namespace_packages(include=[
        "meshagent.*",
    ]),
    python_requires=">=3.9.0",
    install_requires=[
        "pytest>=8.3.4",
        "pytest-asyncio>=0.24.0",
        "openai>=1.66.2",
        "meshagent-api>=0.0.7",
        "meshagent-agents>=0.0.7",
        "meshagent-tools>=0.0.7",
        "playwright~=1.50.0",
        "browserbase~=1.2.0",
        "scrapybara~=2.4.1",
    ],
    package_data={        
        "meshagent.computers": ["py.typed", "*.pyi", "**/*.pyi",  "**/*.js"],
    },
    project_urls={
        "Documentation": "https://meshagent.com",
        "Website": "https://meshagent.com",
        "Source": "https://github.com/meshagent",
    },
)
