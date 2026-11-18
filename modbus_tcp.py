"""Small, dependency-free Modbus TCP client for read-only monitoring."""
from __future__ import annotations
import socket
import struct
import threading

class ModbusError(Exception):
    pass

class ModbusTCPClient:
    def __init__(self, host: str, port: int = 502, unit_id: int = 1, timeout: float = 1.0):
        self.host, self.port, self.unit_id, self.timeout = host, int(port), int(unit_id), float(timeout)
        self._sock = None
        self._lock = threading.Lock()
        self._transaction_id = 0

    def connect(self):
        self.disconnect()
        s = socket.create_connection((self.host, self.port), timeout=self.timeout)
        s.settimeout(self.timeout)
        self._sock = s

    def disconnect(self):
        s, self._sock = self._sock, None
        if s:
            try: s.shutdown(socket.SHUT_RDWR)
            except OSError: pass
            try: s.close()
            except OSError: pass

    @property
    def connected(self):
        return self._sock is not None

    def _recv_exact(self, n: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < n:
            part = self._sock.recv(n - len(chunks))
            if not part:
                raise ModbusError("Conexão encerrada pelo equipamento.")
            chunks.extend(part)
        return bytes(chunks)

    def read(self, function: int, address: int, count: int):
        limits = {1: 2000, 2: 2000, 3: 125, 4: 125}
        if function not in limits:
            raise ModbusError("Função de leitura inválida.")
        if not (0 <= address <= 65535):
            raise ModbusError("Endereço inicial deve estar entre 0 e 65535.")
        if not (1 <= count <= limits[function]):
            raise ModbusError(f"Quantidade inválida para FC{function:02d} (máximo {limits[function]}).")
        if address + count > 65536:
            raise ModbusError("O intervalo solicitado ultrapassa o endereço 65535.")
        with self._lock:
            if not self._sock:
                raise ModbusError("Não conectado.")
            self._transaction_id = (self._transaction_id + 1) & 0xFFFF
            tid = self._transaction_id
            pdu = struct.pack('>BHH', function, address, count)
            mbap = struct.pack('>HHHB', tid, 0, len(pdu) + 1, self.unit_id)
            try:
                self._sock.sendall(mbap + pdu)
                header = self._recv_exact(7)
                r_tid, protocol, length, unit = struct.unpack('>HHHB', header)
                if r_tid != tid:
                    raise ModbusError("Transaction ID da resposta não corresponde à requisição.")
                if protocol != 0:
                    raise ModbusError("Protocol ID inválido na resposta.")
                if unit != self.unit_id:
                    raise ModbusError("Unit ID recebido diferente do solicitado.")
                if length < 2:
                    raise ModbusError("Resposta Modbus incompleta.")
                pdu_resp = self._recv_exact(length - 1)
            except socket.timeout:
                raise ModbusError("Timeout de comunicação.")
            except OSError as exc:
                self.disconnect()
                raise ModbusError(f"Falha de comunicação: {exc}")
            if not pdu_resp:
                raise ModbusError("Resposta PDU vazia.")
            response_fc = pdu_resp[0]
            if response_fc & 0x80:
                code = pdu_resp[1] if len(pdu_resp) > 1 else -1
                names = {1: "Função inválida", 2: "Endereço inválido", 3: "Valor inválido", 4: "Falha do equipamento", 5: "Aguardando processamento", 6: "Equipamento ocupado"}
                raise ModbusError(f"Exceção Modbus {code}: {names.get(code, 'Código de exceção desconhecido')}.")
            if response_fc != function:
                raise ModbusError("Function Code inesperado na resposta.")
            if function in (1, 2):
                byte_count = pdu_resp[1]
                if len(pdu_resp) != 2 + byte_count:
                    raise ModbusError("Tamanho da resposta de bits inconsistente.")
                raw = pdu_resp[2:]
                return [(raw[i // 8] >> (i % 8)) & 1 for i in range(count)]
            byte_count = pdu_resp[1]
            if byte_count != count * 2 or len(pdu_resp) != 2 + byte_count:
                raise ModbusError("Tamanho da resposta de registradores inconsistente.")
            raw = pdu_resp[2:]
            return list(struct.unpack('>' + ('H' * count), raw))
