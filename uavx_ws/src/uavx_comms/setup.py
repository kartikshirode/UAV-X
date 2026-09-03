from setuptools import find_packages, setup

package_name = "uavx_comms"

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
    description="The UAV-X mesh: link model, routing and relay election, "
                "as pure logic with no ROS dependency.",
    license="MIT",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [],
    },
)
