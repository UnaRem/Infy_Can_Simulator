import can
import struct
import time
import threading
import random
import logging

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class InfyModuleSimulator:
    def __init__(self, module_id=0x00, group_id=0x00):
        # --- 1. 设备状态层 (Device State) ---
        self.module_id = module_id     # 模块地址 (0x00~0x3B) [cite: 133]
        self.group_id = group_id       # 组号
        
        # 模拟电气参数
        self.voltage_out = 0.0         # 实际输出电压
        self.current_out = 0.0         # 实际输出电流
        self.set_voltage = 500.0       # 设定电压 (默认)
        self.set_current = 10.0        # 设定电流 (默认)
        self.max_current = 25.6        # 模块最大能力
        self.cap_voltage_max = 750.0
        self.cap_voltage_min = 100.0
        self.cap_power_rated = 15000.0
        
        # 状态位
        self.is_power_on = False       # 开关机状态
        self.is_connected = False      # 通讯状态
        
        # --- CAN配置 ---
        self.bus = None
        self.running = False

    def start(self):
        """启动模拟器"""
        try:
            # 连接 UDP 组播模拟总线
            self.bus = can.interface.Bus(
                channel='224.0.0.1', 
                interface='udp_multicast', 
                port=1234
            )
            self.running = True
            logging.info(f"模拟器启动: 模块ID={hex(self.module_id)}, 组号={self.group_id}")

            # 启动接收线程
            threading.Thread(target=self._receive_loop, daemon=True).start()
            
            # 启动心跳广播线程 (需求: 400ms ± 10ms)
            threading.Thread(target=self._broadcast_heartbeat_loop, daemon=True).start()
            
            # 保持主线程运行
            while self.running:
                time.sleep(1)
        except Exception as e:
            logging.error(f"总线错误: {e}")

    # --- 2. 传输与时序层 (Transport & Timing) ---
    
    def _broadcast_heartbeat_loop(self):
        """
        实现需求1: 0x0757F8XX 报文，400ms上下波动10ms
        文档引用: [cite: 169]
        """
        while self.running:
            # 构造 ID: 0x0757F8 + 模块ID
            # 注意：文档未明确XX定义，通常由源地址填充
            arb_id = 0x0757F800 | self.module_id
            
            msg = can.Message(
                arbitration_id=arb_id,
                data=[0x00] * 8, # 填充数据，文档称上级监控可忽略
                is_extended_id=True
            )
            self.bus.send(msg)
            
            # 400ms = 0.4s. 波动 ±10ms -> 0.390 ~ 0.410s
            sleep_time = random.uniform(0.390, 0.410)
            time.sleep(sleep_time)

    def _receive_loop(self):
        """接收循环"""
        for msg in self.bus:
            if not self.running: break
            if msg.is_error_frame: continue
            
            # 忽略自己发出的消息
            if (msg.arbitration_id & 0xFF) == self.module_id:
                continue
                
            print(f"收到虚拟报文: {msg}")
            self._process_message(msg)

    # --- 3. 协议解析层 (Protocol Parser) ---

    def _process_message(self, msg):
        """
        解析 29位 ID 
        ID结构: Error(3) | Device(4) | CMD(6) | Dest(8) | Src(8)
        """
        can_id = msg.arbitration_id
        
        # 位运算提取字段
        err_code = (can_id >> 26) & 0x07
        device_no = (can_id >> 22) & 0x0F  # 0x0A(单模块) 或 0x0B(组) [cite: 124]
        cmd_no = (can_id >> 16) & 0x3F     # 命令号 [cite: 135]
        dest_addr = (can_id >> 8) & 0xFF
        src_addr = can_id & 0xFF
        
        # 过滤：只处理发给自己的，或者广播(0x3F) 
        # 注意：如果是组播(Device=0x0B)，DestAddr代表组号
        is_for_me = False
        
        if dest_addr == 0x3F: # 广播
            is_for_me = True
        elif device_no == 0x0A and dest_addr == self.module_id: # 点对点
            is_for_me = True
        elif device_no == 0x0B and dest_addr == self.group_id: # 组播
            is_for_me = True
            
        if is_for_me:
            self._route_command(cmd_no, msg.data, src_addr, device_no, dest_addr)

    # --- 4. 指令路由层 (Command Router) ---

    def _route_command(self, cmd, data, remote_src, device_mode, dest_addr):
        """根据命令号分发逻辑 [cite: 135]"""
        
        reply_data = None
        
        # 0x01: 读系统电压电流 (浮点)
        if cmd == 0x01:
            reply_data = self._handle_read_system_float()
            
        # 0x03: 读模块电压电流 (浮点)
        elif cmd == 0x03:
            reply_data = self._handle_read_module_float()
            
        # 0x04: 读模块状态 (Walkin, Temp, Alarms)
        elif cmd == 0x04:
            reply_data = self._handle_read_status()

        elif cmd == 0x08:
            reply_data = self._handle_read_system_fixed()

        elif cmd == 0x09:
            reply_data = self._handle_read_module_fixed()

        elif cmd == 0x0A:
            reply_data = self._handle_read_module_info()

        elif cmd == 0x0C:
            reply_data = self._handle_read_module_external()
            
        # 0x1A: 开关机控制 
        elif cmd == 0x1A:
            self._handle_power_control(data)
            # 广播命令通常无回复，但在点对点模式下需回复状态
            if device_mode == 0x0A and dest_addr != 0x3F:
                reply_data = self._handle_read_power_state()

        # 0x1B: 设模块电压(mV) 总电流(mA) [cite: 161]
        elif cmd == 0x1B:
            self._handle_set_output(data)
            # 根据协议，需回复当前设定值
            reply_data = self._handle_read_output_setting()

        elif cmd == 0x1C:
            self._handle_set_output_fixed(data)
            if dest_addr != 0x3F:
                reply_data = self._handle_read_output_setting()

        # 如果生成了回复数据，则发送
        if reply_data:
            self._send_response(cmd, reply_data, remote_src)

    def _send_response(self, cmd, data, target_addr):
        """构造回复帧"""
        # 构造 ID: Error(0) | Device(0x0A) | CMD | Dest(监控) | Src(我)
        # 监控地址默认为 0xF0 [cite: 131]
        resp_id = (0x00 << 26) | (0x0A << 22) | (cmd << 16) | (target_addr << 8) | self.module_id
        
        msg = can.Message(arbitration_id=resp_id, data=data, is_extended_id=True)
        self.bus.send(msg)
        logging.info(f"回复 CMD={hex(cmd)} 数据={data.hex().upper()}")

    # --- 具体的业务逻辑实现 ---

    def _handle_read_module_float(self):
        """CMD 0x03: 浮点数返回电压电流 [cite: 140]"""
        # IEEE 754 Big Endian
        v_bytes = struct.pack('>f', self.voltage_out)
        c_bytes = struct.pack('>f', self.current_out)
        return v_bytes + c_bytes

    def _handle_read_status(self):
        """CMD 0x04: 模块状态 [cite: 140, 166]"""
        # 简化实现：返回 25度, 无告警
        # Byte0-3: 状态表3,2,1,0
        # Byte4: 环温 (int8)
        temp = 25
        status_bytes = [0x00, 0x00, 0x00, 0x00, temp, 0x00, 0x00, 0x00]
        return bytearray(status_bytes)

    def _handle_power_control(self, data):
        """CMD 0x1A: 开关机 """
        # Byte0: 1=关机, 0=开机
        if data[0] == 0x00:
            self.is_power_on = True
            # 简单模拟：开机后电压升至设定值
            self.voltage_out = self.set_voltage
            self.current_out = self.set_current / 2 # 假装带了一半负载
            logging.info("执行开机")
        else:
            self.is_power_on = False
            self.voltage_out = 0.0
            self.current_out = 0.0
            logging.info("执行关机")

    def _handle_read_power_state(self):
        """辅助回复 0x1A"""
        state = 0x00 if self.is_power_on else 0x01
        return bytearray([state, 0, 0, 0, 0, 0, 0, 0])

    def _handle_set_output(self, data):
        """CMD 0x1B: 设定电压电流 (定点数) [cite: 161]"""
        # Byte0-1: 电压 (mV) MSB
        # Byte2-3: 电流 (mA) MSB
        # 实际上根据协议 0x1B 数据是 Byte0-3 为电压? 
        # 查看: Byte0-3 Voltage(mV), Byte4-7 Current(mA)
        # 文档这里表格有点混淆，需仔细看 Source 162 的 Byte 顺序
        # 0x1B 表格: Byte0(MSB)..Byte3(LSB) 是电压
        
        v_val = struct.unpack('>I', data[0:4])[0] # mV
        c_val = struct.unpack('>I', data[4:8])[0] # mA
        
        self.set_voltage = v_val / 1000.0
        self.set_current = c_val / 1000.0
        
        if self.is_power_on:
            self.voltage_out = self.set_voltage
            
        logging.info(f"设定参数: {self.set_voltage}V, {self.set_current}A")

    def _handle_read_output_setting(self):
        """回复当前设定值"""
        v_int = int(self.set_voltage * 1000)
        c_int = int(self.set_current * 1000)
        return struct.pack('>II', v_int, c_int)

    def _handle_read_system_float(self):
        # 简化：单模块系统，系统电压等于模块电压
        return self._handle_read_module_float()

    def _handle_read_system_fixed(self):
        v_int = max(0, int(self.voltage_out * 1000))
        c_int = max(0, int(self.current_out * 1000))
        return struct.pack('>II', v_int, c_int)

    def _handle_read_module_fixed(self):
        return self._handle_read_system_fixed()

    def _handle_read_module_info(self):
        vmax = int(self.cap_voltage_max * 10)
        vmin = int(self.cap_voltage_min * 10)
        imax = int(self.max_current * 10)
        prate = int(self.cap_power_rated / 10)
        return struct.pack('>HHHH', vmax, vmin, imax, prate)

    def _handle_read_module_external(self):
        ext_v = max(0, int(self.voltage_out * 10))
        allow_i = int(self.max_current * 10) if self.is_power_on else 0
        return struct.pack('>HHHH', ext_v, allow_i, 0, 0)

    def _handle_set_output_fixed(self, data):
        self._handle_set_output(data)

if __name__ == "__main__":
    try:
        # 启动模拟器，假设本模块地址 0x00, 组号 0x00
        sim = InfyModuleSimulator(module_id=0x00, group_id=0x00)
        sim.start()
    except KeyboardInterrupt:
        logging.info("模拟器已停止")
    except Exception as e:
        logging.error(f"启动失败: {e}")
