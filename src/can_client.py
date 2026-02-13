import can
import struct
import threading
import time

class CANClient:
    def __init__(self):
        self.bus = None
        self.running = False
        self.responses = {}
        self.response_lock = threading.Lock()
        
        # 预设命令配置
        self.presets = {
            '1': {'name': '读系统电压电流', 'cmd': 0x01, 'data': None},
            '2': {'name': '读模块电压电流', 'cmd': 0x03, 'data': None},
            '3': {'name': '读模块状态', 'cmd': 0x04, 'data': None},
            '4': {'name': '开机', 'cmd': 0x1A, 'data': bytearray([0x00])},
            '5': {'name': '关机', 'cmd': 0x1A, 'data': bytearray([0x01])},
            '6': {'name': '设定输出500V/10A', 'cmd': 0x1B, 'data': self._make_output_data(500.0, 10.0)},
            '7': {'name': '设定输出400V/8A', 'cmd': 0x1B, 'data': self._make_output_data(400.0, 8.0)},
            '8': {'name': '设定输出600V/15A', 'cmd': 0x1B, 'data': self._make_output_data(600.0, 15.0)},
        }
        
    def _make_output_data(self, voltage, current):
        """构造0x1B命令的数据：电压(mV)和电流(mA)"""
        v_int = int(voltage * 1000)
        c_int = int(current * 1000)
        return struct.pack('>II', v_int, c_int)
    
    def start(self):
        """启动客户端"""
        try:
            self.bus = can.interface.Bus(channel='224.0.0.1', interface='udp_multicast', port='1234')
            self.running = True
            
            # 启动接收线程
            threading.Thread(target=self._receive_loop, daemon=True).start()
            
            print("CAN客户端已启动，连接到模拟器")
            self.show_menu()
            
            # 启动输入处理线程
            threading.Thread(target=self._input_loop, daemon=True).start()
            
            # 保持主线程运行
            while self.running:
                time.sleep(0.1)
                
        except Exception as e:
            print(f"初始化失败: {e}")
            
    def _receive_loop(self):
        """接收响应消息"""
        for msg in self.bus:
            if not self.running:
                break
            if msg.is_error_frame:
                continue
                
            # 解析响应
            self._process_response(msg)
    
    def _process_response(self, msg):
        """处理接收到的响应"""
        can_id = msg.arbitration_id
        
        # 解析ID结构: Error(3) | Device(4) | CMD(6) | Dest(8) | Src(8)
        cmd_no = (can_id >> 16) & 0x3F
        src_addr = can_id & 0xFF
        
        # 存储响应
        with self.response_lock:
            self.responses[cmd_no] = {
                'data': msg.data,
                'src': src_addr,
                'timestamp': time.time()
            }
        
        # 简洁显示响应
        response_text = self._format_response_text(cmd_no, msg.data)
        print(f"\r>>> 响应: {response_text}", end="", flush=True)
        print()  # 换行但保持菜单可见
    
    def _format_response_text(self, cmd, data):
        """格式化响应文本为简洁格式"""
        if cmd == 0x01 or cmd == 0x03:  # 电压电流浮点数
            if len(data) >= 8:
                voltage = struct.unpack('>f', data[0:4])[0]
                current = struct.unpack('>f', data[4:8])[0]
                return f"电压{voltage:.1f}V 电流{current:.1f}A"
            else:
                return f"CMD=0x{cmd:02X} 数据长度不足"
                
        elif cmd == 0x04:  # 模块状态
            if len(data) >= 5:
                temp = data[4] if data[4] < 128 else data[4] - 256
                return f"温度{temp}°C 状态正常"
            else:
                return f"CMD=0x{cmd:02X} 数据长度不足"
                
        elif cmd == 0x1A:  # 开关机状态
            if len(data) >= 1:
                state = "开机" if data[0] == 0x00 else "关机"
                return f"状态: {state}"
            else:
                return f"CMD=0x{cmd:02X} 数据长度不足"
                
        elif cmd == 0x1B:  # 输出设定
            if len(data) >= 8:
                v_int = struct.unpack('>I', data[0:4])[0]
                c_int = struct.unpack('>I', data[4:8])[0]
                voltage = v_int / 1000.0
                current = c_int / 1000.0
                return f"设定{voltage:.1f}V/{current:.1f}A"
            else:
                return f"CMD=0x{cmd:02X} 数据长度不足"
        else:
            # 心跳包或其他未知命令
            return f"CMD=0x{cmd:02X} 心跳"
    
    def send_command(self, cmd, data=None, target_addr=0x00):
        """发送CAN命令"""
        try:
            # 构造ID: Error(0) | Device(0x0A) | CMD | Dest(模块) | Src(监控)
            # 监控地址使用 0xF0
            can_id = (0x00 << 26) | (0x0A << 22) | (cmd << 16) | (target_addr << 8) | 0xF0
            
            if data is None:
                data = bytearray([0x00] * 8)
            
            msg = can.Message(arbitration_id=can_id, data=data, is_extended_id=True)
            self.bus.send(msg)
            
            print(f"[发送] CMD=0x{cmd:02X}, 目标地址=0x{target_addr:02X}, 数据={data.hex().upper()}")
            return True
            
        except Exception as e:
            print(f"发送失败: {e}")
            return False
    
    def show_menu(self):
        """显示命令菜单"""
        print("\n=== CAN总线命令客户端 ===")
        print("预设命令:")
        for num, preset in self.presets.items():
            print(f"  {num}. {preset['name']}")
        print("  q. 退出")
        print("========================")
    
    def _input_loop(self):
        """输入处理循环"""
        while self.running:
            try:
                user_input = input("\n请输入命令编号: ").strip()
                
                if user_input.lower() == 'q':
                    self.running = False
                    return
                
                if user_input in self.presets:
                    preset = self.presets[user_input]
                    cmd = preset['cmd']
                    data = preset['data']
                    
                    # 发送命令
                    self.send_command(cmd, data)
                    
                    # 等待响应
                    time.sleep(0.1)  # 给模拟器一点处理时间
                    
                else:
                    print("无效的命令编号，请重新选择")
                    
            except KeyboardInterrupt:
                self.running = False
                break
            except Exception as e:
                print(f"输入处理错误: {e}")
    
    def handle_user_input(self):
        """处理用户输入（保留兼容性）"""
        pass

def main():
    client = CANClient()
    try:
        client.start()
    except KeyboardInterrupt:
        print("\n客户端已停止")
    finally:
        if client.bus:
            client.bus.shutdown()

if __name__ == "__main__":
    main()