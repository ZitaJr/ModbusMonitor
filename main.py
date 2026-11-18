from __future__ import annotations
import csv, json, queue, threading, time, sys
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from core.modbus_tcp import ModbusTCPClient, ModbusError


APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / 'settings.json'
DEFAULTS = {'host':'192.168.0.2','port':502,'unit_id':1,'timeout':1.0,'function':2,'address':0,'count':32,'interval':1.0}

class App:
    def __init__(self, root):
        self.root=root; root.title('Modbus Monitor - Portable'); root.geometry('1120x700'); root.minsize(900,560)
        root.configure(bg='#171b20')
        self.client=None; self.polling=False; self.worker=None; self.q=queue.Queue(); self.rows=[]; self.values=[]
        self.cfg=self.load_cfg()
        self.vars={k:tk.StringVar(value=str(v)) for k,v in self.cfg.items()}
        self.status=tk.StringVar(value='DESCONECTADO'); self.last_update=tk.StringVar(value='—')
        self.setup_style(); self.build_ui(); self.root.protocol('WM_DELETE_WINDOW', self.close); self.tick_queue()

    def load_cfg(self):
        try: return {**DEFAULTS, **json.loads(CONFIG_PATH.read_text(encoding='utf-8'))}
        except Exception: return DEFAULTS.copy()
    def save_cfg(self):
        try: CONFIG_PATH.write_text(json.dumps({k:self.vars[k].get() for k in DEFAULTS},indent=2),encoding='utf-8')
        except Exception: pass
    def setup_style(self):
        st=ttk.Style(); st.theme_use('clam')
        st.configure('TFrame',background='#171b20'); st.configure('TLabel',background='#171b20',foreground='#dce3ea')
        st.configure('TLabelframe',background='#171b20',foreground='#dce3ea'); st.configure('TLabelframe.Label',background='#171b20',foreground='#dce3ea')
        st.configure('TButton',background='#2c343d',foreground='#e7edf3',padding=6); st.map('TButton',background=[('active','#3c4854')])
        st.configure('TEntry',fieldbackground='#232a32',foreground='#e7edf3')
        st.configure('TCombobox',fieldbackground='#232a32',foreground='#e7edf3')
        st.configure('Treeview',background='#20262d',foreground='#e7edf3',fieldbackground='#20262d',rowheight=25)
        st.configure('Treeview.Heading',background='#303943',foreground='#e7edf3')
    def build_ui(self):
        top=ttk.Frame(self.root,padding=(12,10)); top.pack(fill='x')
        ttk.Label(top,text='MODBUS MONITOR',font=('Segoe UI',18,'bold')).pack(side='left')
        ttk.Label(top,text='PORTABLE  •  READ ONLY',font=('Segoe UI',9)).pack(side='left',padx=14)
        ttk.Label(top,textvariable=self.status,font=('Segoe UI',10,'bold')).pack(side='right')
        cfg=ttk.LabelFrame(self.root,text='Conexão e leitura',padding=10); cfg.pack(fill='x',padx=12,pady=(0,8))
        fields=[('IP / Host','host',16),('Porta','port',6),('Unit ID','unit_id',5),('Timeout (s)','timeout',6),('Função','function',8),('Endereço inicial','address',8),('Quantidade','count',8),('Intervalo (s)','interval',6)]
        for i,(label,key,width) in enumerate(fields):
            col=i*2; ttk.Label(cfg,text=label).grid(row=0,column=col,sticky='w',padx=(0,4))
            if key=='function':
                cb=ttk.Combobox(cfg,textvariable=self.vars[key],values=['1','2','3','4'],width=width,state='readonly'); cb.grid(row=1,column=col,sticky='w',padx=(0,10))
            else: ttk.Entry(cfg,textvariable=self.vars[key],width=width).grid(row=1,column=col,sticky='w',padx=(0,10))
        btns=ttk.Frame(cfg); btns.grid(row=2,column=0,columnspan=16,sticky='w',pady=(10,0))
        ttk.Button(btns,text='CONNECT',command=self.connect).pack(side='left',padx=(0,6)); ttk.Button(btns,text='DISCONNECT',command=self.disconnect).pack(side='left',padx=6)
        ttk.Button(btns,text='READ ONCE',command=self.read_once).pack(side='left',padx=6); ttk.Button(btns,text='START MONITORING',command=self.start_poll).pack(side='left',padx=6); ttk.Button(btns,text='STOP',command=self.stop_poll).pack(side='left',padx=6); ttk.Button(btns,text='EXPORT CSV',command=self.export_csv).pack(side='left',padx=6)
        info=ttk.Frame(self.root,padding=(12,0,12,8)); info.pack(fill='x'); ttk.Label(info,text='FC01 Coils  |  FC02 Discrete Inputs  |  FC03 Holding Registers  |  FC04 Input Registers').pack(side='left'); ttk.Label(info,text='Atualização: ').pack(side='right'); ttk.Label(info,textvariable=self.last_update).pack(side='right')
        table_frame=ttk.Frame(self.root,padding=(12,0,12,12)); table_frame.pack(fill='both',expand=True)
        cols=('address','type','decimal','hex','binary','status'); self.tree=ttk.Treeview(table_frame,columns=cols,show='headings')
        headers={'address':'Endereço','type':'Tipo','decimal':'Decimal / Estado','hex':'Hexadecimal','binary':'Binário','status':'Status'}
        widths={'address':90,'type':140,'decimal':160,'hex':140,'binary':390,'status':150}
        for c in cols: self.tree.heading(c,text=headers[c]); self.tree.column(c,width=widths[c],anchor='center')
        y=ttk.Scrollbar(table_frame,orient='vertical',command=self.tree.yview); x=ttk.Scrollbar(table_frame,orient='horizontal',command=self.tree.xview); self.tree.configure(yscrollcommand=y.set,xscrollcommand=x.set)
        self.tree.pack(side='left',fill='both',expand=True); y.pack(side='right',fill='y'); x.pack(side='bottom',fill='x')
        self.log=tk.Text(self.root,height=5,bg='#11151a',fg='#b8c5d2',insertbackground='white',state='disabled',font=('Consolas',9)); self.log.pack(fill='x',padx=12,pady=(0,12))
        self.log_msg('Software iniciado. Configure a conexão e clique em CONNECT.')
    def log_msg(self,msg):
        self.log.configure(state='normal'); self.log.insert('end',f'[{time.strftime("%H:%M:%S")}] {msg}\n'); self.log.see('end'); self.log.configure(state='disabled')
    def get_settings(self):
        try:
            s={k:self.vars[k].get() for k in DEFAULTS}; s['port']=int(s['port']); s['unit_id']=int(s['unit_id']); s['timeout']=float(s['timeout']); s['function']=int(s['function']); s['address']=int(s['address']); s['count']=int(s['count']); s['interval']=float(s['interval'])
            if not 1<=s['port']<=65535: raise ValueError('Porta fora de faixa (1–65535).')
            if not 0<=s['unit_id']<=255: raise ValueError('Unit ID deve estar entre 0 e 255.')
            if s['timeout']<=0 or s['interval']<=0: raise ValueError('Timeout e intervalo devem ser maiores que zero.')
            limits={1:2000,2:2000,3:125,4:125}
            if not 1<=s['count']<=limits[s['function']]: raise ValueError(f'Quantidade máxima para FC{s["function"]:02d} é {limits[s["function"]]}.')
            if not 0<=s['address']<=65535 or s['address']+s['count']>65536: raise ValueError('Endereço/intervalo inválido.')
            return s
        except Exception as e: messagebox.showerror('Configuração inválida',str(e)); return None
    def connect(self):
        s=self.get_settings()
        if not s:return
        self.save_cfg(); self.disconnect(silent=True)
        try:
            self.client=ModbusTCPClient(s['host'],s['port'],s['unit_id'],s['timeout']); self.client.connect()
            self.status.set('CONECTADO'); self.log_msg(f'Conectado a {s["host"]}:{s["port"]} | Unit ID {s["unit_id"]}.')
        except Exception as e: self.client=None; self.status.set('ERRO DE CONEXÃO'); self.log_msg(str(e)); messagebox.showerror('Conexão',str(e))
    def disconnect(self,silent=False):
        self.stop_poll()
        if self.client:self.client.disconnect(); self.client=None
        self.status.set('DESCONECTADO')
        if not silent:self.log_msg('Desconectado.')
    def read_once(self):
        if not self.client:self.connect()
        if not self.client:return
        s=self.get_settings()
        if not s:return
        threading.Thread(target=self.do_read,args=(s,),daemon=True).start()
    def start_poll(self):
        if not self.client:self.connect()
        if not self.client:return
        s=self.get_settings()
        if not s:return
        self.stop_poll(); self.polling=True
        self.worker=threading.Thread(target=self.poll_loop,args=(s,),daemon=True); self.worker.start(); self.log_msg('Monitoramento cíclico iniciado.')
    def stop_poll(self):
        self.polling=False
    def poll_loop(self,s):
        while self.polling:
            start=time.monotonic(); self.do_read(s)
            wait=max(0.05,s['interval']-(time.monotonic()-start)); time.sleep(wait)
    def do_read(self,s):
        try:
            vals=self.client.read(s['function'],s['address'],s['count'])
            self.q.put(('data',s,vals,time.strftime('%H:%M:%S')))
        except Exception as e:self.q.put(('error',str(e)))
    def tick_queue(self):
        try:
            while True:
                item=self.q.get_nowait()
                if item[0]=='data': self.show_data(item[1],item[2],item[3])
                else: self.status.set('FALHA DE LEITURA'); self.log_msg(item[1])
        except queue.Empty: pass
        self.root.after(100,self.tick_queue)
    def show_data(self,s,vals,stamp):
        self.tree.delete(*self.tree.get_children()); self.values=[]; fc=s['function']; base=s['address']
        for i,v in enumerate(vals):
            addr=base+i
            if fc in (1,2): typ='Coil' if fc==1 else 'Discrete Input'; dec=str(bool(v)); hx=f'{v:X}'; binary=str(v)
            else: typ='Holding Register' if fc==3 else 'Input Register'; dec=str(v); hx=f'0x{v:04X}'; binary=f'{v:016b}'
            status='OK'; row=(addr,typ,dec,hx,binary,status); self.tree.insert('', 'end',values=row); self.values.append(row)
        self.status.set('CONECTADO • LEITURA OK'); self.last_update.set(stamp)
    def export_csv(self):
        if not self.values: messagebox.showinfo('Exportação','Nenhuma leitura disponível para exportar.'); return
        path=filedialog.asksaveasfilename(defaultextension='.csv',filetypes=[('CSV','*.csv')],initialfile='modbus_read.csv')
        if not path:return
        try:
            with open(path,'w',newline='',encoding='utf-8-sig') as f:
                w=csv.writer(f,delimiter=';'); w.writerow(['Endereco','Tipo','Decimal/Estado','Hexadecimal','Binario','Status']); w.writerows(self.values)
            self.log_msg(f'CSV exportado: {path}'); messagebox.showinfo('Exportação','Arquivo CSV salvo com sucesso.')
        except Exception as e:messagebox.showerror('Exportação',str(e))
    def close(self): self.stop_poll(); self.disconnect(silent=True); self.save_cfg(); self.root.destroy()

if __name__=='__main__':
    root=tk.Tk(); App(root); root.mainloop()
