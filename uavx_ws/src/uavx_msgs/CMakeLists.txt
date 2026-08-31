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

ament_export_dependencies(rosidl_default_runtime)
ament_package()
