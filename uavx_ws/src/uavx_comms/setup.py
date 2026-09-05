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
    description="The UAV-X mesh: the link model, the routing and election "
                "state machines, and the two nodes that wire them to the "
                "tx and rx seam.",
    license="MIT",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "router = uavx_comms.router_node:main",
            "link_layer = uavx_comms.link_layer:main",
        ],
    },
)
