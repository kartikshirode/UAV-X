cmake_minimum_required(VERSION 3.8)
project(uavx_msgs)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)

# The order here does not matter, but the field order inside each .msg does.
# scripts/check_message_contract.py compares `ros2 interface show` line for
# line against the fenced blocks in stage-1/architecture.md section 1b, so a
# reordered field is a failed gate rather than a cosmetic change.
rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/SwarmPacket.msg"
  "msg/Hello.msg"
  "msg/LinkState.msg"
  "msg/RoleAssignment.msg"
  "msg/RunMetrics.msg"
)

# 4 September, the first full run of the week 1 gate. This package built,
# generated its bindings and reported zero tests, and the gate_test fix from
# week 2 refuses a package that reports zero tests, so W1.1 failed on the
# first chunk of the first week. The question plan.md asks of 1.1 is whether
# the five types resolve and SwarmPacket carries its identity fields, and a
# package with no tests cannot answer it. test/test_interfaces.py does, from
# the importing side, against the generated Python bindings.
if(BUILD_TESTING)
  find_package(ament_cmake_pytest REQUIRED)
  ament_add_pytest_test(test_interfaces test/test_interfaces.py
    TIMEOUT 60
  )
endif()

ament_export_dependencies(rosidl_default_runtime)
ament_package()
