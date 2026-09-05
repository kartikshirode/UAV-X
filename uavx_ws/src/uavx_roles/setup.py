from setuptools import find_packages, setup

package_name = "uavx_roles"

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
    description="The half of the role protocol that moves an aircraft: one "
                "vehicle's grants, where they put it, and what its role did.",
    license="MIT",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "role_manager = uavx_roles.role_manager:main",
        ],
    },
)
