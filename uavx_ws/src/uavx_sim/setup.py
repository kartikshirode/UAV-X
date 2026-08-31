from setuptools import find_packages, setup

package_name = "uavx_sim"

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
    description="The UAV-X simulation harness: scenarios, injection, "
                "graph capture, resource sampling and the run record.",
    license="MIT",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "scenario_runner = uavx_sim.scenario_runner:main",
        ],
    },
)
