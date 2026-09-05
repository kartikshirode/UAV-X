from setuptools import find_packages, setup

package_name = "uavx_gcs"

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
    description="The UAV-X ground station: one tx endpoint, one rx endpoint, "
                "and the ledger every delivery claim is read from.",
    license="MIT",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "gcs_node = uavx_gcs.gcs_node:main",
        ],
    },
)
