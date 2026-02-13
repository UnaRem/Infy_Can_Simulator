import tkinter as tk
from tkinter import ttk, scrolledtext
import can
import struct
import threading
import time
import queue
from datetime import datetime

class CANClientGUI:
    def __init__(self):
        self.window = tk.Tk()
        self.window.title("CAN总线客户端")
        self.window.geometry("800x600")
        self.window.resizable(True, True)
        
        # CAN总线相关
        self.bus = None
        self.running = False
        
        # 心跳状态相关
        self.heartbeat_count = 0
        self.last_heartbeat_time = 0
        self.heartbeat_interval = 0.4  # 400ms
        self.heartbeat_status = "无心跳"
        self.timeout_check_id = None  # 存储超时检查的定时器ID
        
        # UI线程消息队列（避免跨线程直接操作Tk）
        self.ui_queue = queue.Queue()
        self.ui_poll_ms = 50
        self.ui_poll_id = None
        
        # 预设命令配置
        self.presets = [
            ("读系统电压电流", 0x01, None),
            ("读模块电压电流", 0x03, None),
            ("读模块状态", 0x04, None),
            ("开机", 0x1A, bytearray([0x00])),
            ("关机", 0x1A, bytearray([0x01])),
            ("设定500V/10A", 0x1B, self.make_output_data(500.0, 10.0)),
            ("设定400V/8A", 0x1B, self.make_output_data(400.0, 8.0)),
            ("设定600V/15A", 0x1B, self.make_output_data(600.0, 15.0)),
        ]
        
        self.setup_ui()
        self.setup_can()
        self.start_ui_polling()
        
    def make_output_data(self, voltage, current):
        """构造0x1B命令的数据：电压(mV)和电流(mA)"""
        v_int = int(voltage * 1000)
        c_int = int(current * 1000)
        return struct.pack('>II', v_int, c_int)
    
    def setup_ui(self):
        """设置UI界面"""
        # 主框架
        main_frame = ttk.Frame(self.window, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # 配置网格权重
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(0, weight=1)
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(1, weight=1)
        
        # 标题
        title_label = ttk.Label(main_frame, text="CAN总线命令客户端", 
                               font=('Arial', 14, 'bold'))
        title_label.grid(row=0, column=0, pady=(0, 10))
        
        # 按钮区域
        self.create_button_area(main_frame)
        
        # 响应显示区域
        self.create_response_area(main_frame)
        
        # 状态栏
        self.create_status_bar(main_frame)
    
    def create_button_area(self, parent):
        """创建按钮区域"""
        button_frame = ttk.LabelFrame(parent, text="预设命令", padding="10")
        button_frame.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        
        # 创建3列网格的按钮
        for i, (name, cmd, data) in enumerate(self.presets):
            row = i // 3
            col = i % 3
            
            btn = ttk.Button(button_frame, text=name, width=20,
                           command=lambda c=cmd, d=data, n=name: self.send_command(c, d, n))
            btn.grid(row=row, column=col, padx=5, pady=5)
    
    def create_response_area(self, parent):
        """创建响应显示区域"""
        # 创建左右分栏
        paned_window = ttk.PanedWindow(parent, orient=tk.HORIZONTAL)
        paned_window.grid(row=2, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), pady=(0, 10))
        paned_window.columnconfigure(0, weight=3)
        paned_window.columnconfigure(1, weight=1)
        
        # 左侧：响应记录
        response_frame = ttk.LabelFrame(paned_window, text="响应记录", padding="10")
        paned_window.add(response_frame, weight=3)
        response_frame.columnconfigure(0, weight=1)
        response_frame.rowconfigure(0, weight=1)
        
        self.response_text = scrolledtext.ScrolledText(response_frame, height=15, width=60)
        self.response_text.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        clear_btn = ttk.Button(response_frame, text="清除记录", command=self.clear_responses)
        clear_btn.grid(row=1, column=0, pady=(5, 0), sticky=tk.E)
        
        # 右侧：心跳状态面板
        heartbeat_frame = ttk.LabelFrame(paned_window, text="心跳状态", padding="10")
        paned_window.add(heartbeat_frame, weight=1)
        
        self.create_heartbeat_panel(heartbeat_frame)
    
    def create_heartbeat_panel(self, parent):
        """创建心跳状态面板"""
        # 心跳指示器
        indicator_frame = ttk.Frame(parent)
        indicator_frame.pack(pady=(0, 10))
        
        self.heartbeat_indicator = tk.Canvas(indicator_frame, width=20, height=20, bg='lightgray')
        self.heartbeat_indicator.pack(side=tk.LEFT, padx=(0, 5))
        
        self.heartbeat_status_label = ttk.Label(indicator_frame, text="无心跳", font=('Arial', 10, 'bold'))
        self.heartbeat_status_label.pack(side=tk.LEFT)
        
        # 心跳统计信息
        stats_frame = ttk.Frame(parent)
        stats_frame.pack(fill=tk.BOTH, expand=True)
        
        # 心跳计数
        count_frame = ttk.Frame(stats_frame)
        count_frame.pack(fill=tk.X, pady=2)
        ttk.Label(count_frame, text="心跳次数:").pack(side=tk.LEFT)
        self.heartbeat_count_label = ttk.Label(count_frame, text="0", font=('Arial', 10, 'bold'))
        self.heartbeat_count_label.pack(side=tk.RIGHT)
        
        # 最后心跳时间
        time_frame = ttk.Frame(stats_frame)
        time_frame.pack(fill=tk.X, pady=2)
        ttk.Label(time_frame, text="最后心跳:").pack(side=tk.LEFT)
        self.last_heartbeat_label = ttk.Label(time_frame, text="--:--:--", font=('Arial', 9))
        self.last_heartbeat_label.pack(side=tk.RIGHT)
        
        # 心跳间隔
        interval_frame = ttk.Frame(stats_frame)
        interval_frame.pack(fill=tk.X, pady=2)
        ttk.Label(interval_frame, text="间隔:").pack(side=tk.LEFT)
        self.interval_label = ttk.Label(interval_frame, text="--ms", font=('Arial', 9))
        self.interval_label.pack(side=tk.RIGHT)
        
        # 心跳频率图表（简化版）
        chart_frame = ttk.LabelFrame(stats_frame, text="心跳频率", padding="5")
        chart_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        
        self.heartbeat_chart = tk.Canvas(chart_frame, height=100, bg='white')
        self.heartbeat_chart.pack(fill=tk.BOTH, expand=True)
        
        # 初始化图表数据
        self.heartbeat_history = []
        self.max_history_points = 50
    
    def create_status_bar(self, parent):
        """创建状态栏"""
        status_frame = ttk.Frame(parent)
        status_frame.grid(row=3, column=0, sticky=(tk.W, tk.E), pady=(10, 0))
        
        self.status_label = ttk.Label(status_frame, text="状态: 未连接", 
                                     foreground="red")
        self.status_label.pack(side=tk.LEFT)
        
        # 连接/断开按钮
        self.connect_btn = ttk.Button(status_frame, text="连接", 
                                      command=self.toggle_connection)
        self.connect_btn.pack(side=tk.RIGHT)
    
    def setup_can(self):
        """设置CAN总线"""
        try:
            self.bus = can.interface.Bus(channel='224.0.0.1', 
                                        interface='udp_multicast', 
                                        port=1234)
            self.running = True
            self.status_label.config(text="状态: 已连接", foreground="green")
            self.connect_btn.config(text="断开")
            
            # 启动接收线程
            threading.Thread(target=self.receive_loop, daemon=True).start()
            
            self.add_response("系统", "CAN总线连接成功")
            
        except Exception as e:
            self.add_response("错误", f"连接失败: {e}")
            self.status_label.config(text="状态: 连接失败", foreground="red")
    
    def toggle_connection(self):
        """切换连接状态"""
        if self.running:
            self.disconnect()
        else:
            self.setup_can()
    
    def disconnect(self):
        """断开连接"""
        self.running = False
        
        # 清理定时器
        if self.timeout_check_id:
            self.window.after_cancel(self.timeout_check_id)
            self.timeout_check_id = None
        
        if self.bus:
            self.bus.shutdown()
            self.bus = None
        
        self.status_label.config(text="状态: 已断开", foreground="red")
        self.connect_btn.config(text="连接")
        self.add_response("系统", "CAN总线已断开")
        
        # 重置心跳状态
        self.heartbeat_indicator.delete("all")
        self.heartbeat_indicator.create_oval(2, 2, 18, 18, fill='lightgray', outline='gray')
        self.heartbeat_status_label.config(text="无心跳", foreground='gray')
    
    def send_command(self, cmd, data, name):
        """发送CAN命令"""
        if not self.running or not self.bus:
            self.add_response("错误", "未连接到CAN总线")
            return
        
        try:
            # 构造ID: Error(0) | Device(0x0A) | CMD | Dest(模块) | Src(监控)
            can_id = (0x00 << 26) | (0x0A << 22) | (cmd << 16) | (0x00 << 8) | 0xF0
            
            if data is None:
                data = bytearray([0x00] * 8)
            
            msg = can.Message(arbitration_id=can_id, data=data, is_extended_id=True)
            self.bus.send(msg)
            
            self.add_response("发送", f"{name} (CMD=0x{cmd:02X})")
            
        except Exception as e:
            self.add_response("错误", f"发送失败: {e}")
    
    def receive_loop(self):
        """接收循环"""
        try:
            for msg in self.bus:
                if not self.running:
                    break
                if msg.is_error_frame:
                    continue
                
                # 将消息投递到UI线程处理
                self.ui_queue.put({
                    "type": "msg",
                    "msg": msg,
                    "ts": time.time()
                })
        except Exception as e:
            if self.running:  # 只有在运行状态下才报告错误
                self.ui_queue.put({
                    "type": "error",
                    "text": f"接收循环异常: {e}"
                })
                self.ui_queue.put({"type": "disconnect"})
    
    def process_response(self, msg, recv_time=None):
        """处理接收到的响应"""
        can_id = msg.arbitration_id
        
        # 解析ID结构
        cmd_no = (can_id >> 16) & 0x3F
        src_addr = can_id & 0xFF
        
        # 检查是否为心跳包 (0x17)
        if cmd_no == 0x17:
            self.update_heartbeat_status(recv_time)
        else:
            # 其他命令的响应
            response_text = self.format_response_text(cmd_no, msg.data)
            self.add_response("接收", f"CMD=0x{cmd_no:02X} - {response_text}")
    
    def update_heartbeat_status(self, current_time=None):
        """更新心跳状态"""
        if current_time is None:
            current_time = time.time()
        
        # 更新心跳计数
        self.heartbeat_count += 1
        
        # 计算心跳间隔
        if self.last_heartbeat_time > 0:
            interval = (current_time - self.last_heartbeat_time) * 1000  # 转换为毫秒
            self.heartbeat_history.append(interval)
            
            # 保持历史记录在限制范围内
            if len(self.heartbeat_history) > self.max_history_points:
                self.heartbeat_history.pop(0)
            
            # 更新间隔显示
            self.interval_label.config(text=f"{interval:.0f}ms")
            
            # 更新图表
            self.update_heartbeat_chart()
        
        self.last_heartbeat_time = current_time
        
        # 更新UI显示
        self.heartbeat_count_label.config(text=str(self.heartbeat_count))
        self.last_heartbeat_label.config(text=datetime.now().strftime("%H:%M:%S"))
        
        # 更新指示器颜色和状态
        self.heartbeat_indicator.delete("all")
        self.heartbeat_indicator.create_oval(2, 2, 18, 18, fill='green', outline='darkgreen')
        self.heartbeat_status_label.config(text="心跳正常", foreground='green')
        
        # 取消之前的超时检查定时器，避免累积
        if self.timeout_check_id:
            self.window.after_cancel(self.timeout_check_id)
        
        # 设置新的超时检测
        self.timeout_check_id = self.window.after(1000, self.check_heartbeat_timeout)
    
    def check_heartbeat_timeout(self):
        """检查心跳超时"""
        if not self.running:
            return
            
        current_time = time.time()
        time_since_last = (current_time - self.last_heartbeat_time) * 1000
        
        # 如果超过1秒没有心跳，标记为异常
        if time_since_last > 1000:
            self.heartbeat_indicator.delete("all")
            self.heartbeat_indicator.create_oval(2, 2, 18, 18, fill='red', outline='darkred')
            self.heartbeat_status_label.config(text="心跳超时", foreground='red')
    
    def update_heartbeat_chart(self):
        """更新心跳频率图表"""
        try:
            if not self.heartbeat_history:
                return
                
            # 清除图表
            self.heartbeat_chart.delete("all")
            
            # 获取画布尺寸
            width = self.heartbeat_chart.winfo_width()
            height = self.heartbeat_chart.winfo_height()
            
            if width <= 1 or height <= 1:
                return
            
            # 绘制网格线
            for i in range(0, height, 20):
                self.heartbeat_chart.create_line(0, i, width, i, fill='lightgray', dash=(2, 2))
            
            # 绘制心跳间隔曲线
            if len(self.heartbeat_history) > 1:
                points = []
                for i, interval in enumerate(self.heartbeat_history):
                    x = (i / (self.max_history_points - 1)) * width
                    # 将间隔映射到图表高度 (300ms-500ms 映射到 20-80像素)
                    y = height - 20 - ((interval - 300) / 200) * (height - 40)
                    y = max(20, min(height - 20, y))  # 限制在图表范围内
                    points.extend([x, y])
                
                if len(points) >= 4:
                    self.heartbeat_chart.create_line(points, fill='blue', width=2)
            
            # 添加标签
            self.heartbeat_chart.create_text(5, 10, text="500ms", anchor='nw', font=('Arial', 8))
            self.heartbeat_chart.create_text(5, height - 10, text="300ms", anchor='sw', font=('Arial', 8))
        except Exception as e:
            # 图表更新失败不影响主要功能
            pass

    def start_ui_polling(self):
        """启动UI线程消息轮询"""
        if self.ui_poll_id:
            self.window.after_cancel(self.ui_poll_id)
        self.ui_poll_id = self.window.after(self.ui_poll_ms, self.process_ui_queue)

    def process_ui_queue(self):
        """在UI线程处理消息队列"""
        try:
            while True:
                item = self.ui_queue.get_nowait()
                item_type = item.get("type")
                if item_type == "msg":
                    self.process_response(item.get("msg"), item.get("ts"))
                elif item_type == "error":
                    self.add_response("错误", item.get("text"))
                elif item_type == "disconnect":
                    self.disconnect()
                elif item_type == "system":
                    self.add_response("系统", item.get("text"))
        except queue.Empty:
            pass
        
        try:
            if self.window.winfo_exists():
                self.ui_poll_id = self.window.after(self.ui_poll_ms, self.process_ui_queue)
        except tk.TclError:
            pass
    
    def format_response_text(self, cmd, data):
        """格式化响应文本"""
        if cmd == 0x01 or cmd == 0x03:  # 电压电流浮点数
            if len(data) >= 8:
                voltage = struct.unpack('>f', data[0:4])[0]
                current = struct.unpack('>f', data[4:8])[0]
                return f"电压{voltage:.2f}V, 电流{current:.2f}A"
            else:
                return f"数据长度不足"
                
        elif cmd == 0x04:  # 模块状态
            if len(data) >= 5:
                temp = data[4] if data[4] < 128 else data[4] - 256
                return f"温度{temp}°C, 状态正常"
            else:
                return f"数据长度不足"
                
        elif cmd == 0x1A:  # 开关机状态
            if len(data) >= 1:
                state = "开机" if data[0] == 0x00 else "关机"
                return f"状态: {state}"
            else:
                return f"数据长度不足"
                
        elif cmd == 0x1B:  # 输出设定
            if len(data) >= 8:
                v_int = struct.unpack('>I', data[0:4])[0]
                c_int = struct.unpack('>I', data[4:8])[0]
                voltage = v_int / 1000.0
                current = c_int / 1000.0
                return f"设定{voltage:.1f}V/{current:.1f}A"
            else:
                return f"数据长度不足"
        else:
            # 心跳包或其他未知命令
            return f"心跳包"
    
    def add_response(self, type_text, content):
        """添加响应记录"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        # 根据类型设置颜色标签
        if type_text == "发送":
            tag = "send"
        elif type_text == "接收":
            tag = "receive"
        elif type_text == "错误":
            tag = "error"
        else:
            tag = "system"
        
        # 插入文本
        self.response_text.insert(tk.END, f"[{timestamp}] {type_text}: {content}\n", tag)
        self.response_text.see(tk.END)  # 自动滚动到底部
    
    def clear_responses(self):
        """清除响应记录"""
        self.response_text.delete(1.0, tk.END)
        self.add_response("系统", "响应记录已清除")
    
    def run(self):
        """运行GUI"""
        # 配置文本标签颜色
        self.response_text.tag_config("send", foreground="blue")
        self.response_text.tag_config("receive", foreground="green")
        self.response_text.tag_config("error", foreground="red")
        self.response_text.tag_config("system", foreground="gray")
        
        # 处理窗口关闭事件
        self.window.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        # 启动主循环
        self.window.mainloop()
    
    def on_closing(self):
        """窗口关闭时的处理"""
        self.disconnect()
        self.window.destroy()

def main():
    app = CANClientGUI()
    app.run()

if __name__ == "__main__":
    main()
