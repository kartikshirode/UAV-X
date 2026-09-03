from setuptools import find_packages, setup

package_name = "uavx_eval"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages",
         ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="kartikshirode",
    maintainer_email="megamindsresearch@gmail.com",
    description="The UAV-X observer: the ground-truth metrics collector and "
                "the run record provenance gate.",
    license="MIT",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "metrics_collector = uavx_eval.metrics_collector:main",
        ],
    },
)
