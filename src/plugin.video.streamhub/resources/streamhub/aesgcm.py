_SBOX = []
_INV = []
_RCON = []


def _init_tables():
	p = 1
	q = 1
	sbox = [0] * 256
	first = True
	while first or p != 1:
		first = False
		p = p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)
		q ^= q << 1
		q ^= q << 2
		q ^= q << 4
		q &= 0xFF
		if q & 0x80:
			q ^= 0x09
		xformed = q ^ ((q << 1) | (q >> 7)) ^ ((q << 2) | (q >> 6)) ^ \
			((q << 3) | (q >> 5)) ^ ((q << 4) | (q >> 4))
		sbox[p] = (xformed ^ 0x63) & 0xFF
	sbox[0] = 0x63
	_SBOX.extend(sbox)
	rcon = [1]
	for _ in range(1, 15):
		v = rcon[-1] << 1
		if v & 0x100:
			v ^= 0x11B
		rcon.append(v & 0xFF)
	_RCON.extend(rcon)


_init_tables()


def _xtime(a):
	a <<= 1
	if a & 0x100:
		a ^= 0x11B
	return a & 0xFF


def _mul(a, b):
	res = 0
	for _ in range(8):
		if b & 1:
			res ^= a
		b >>= 1
		a = _xtime(a)
	return res & 0xFF


def _key_expansion(key):
	nk = len(key) // 4
	nr = nk + 6
	w = [list(key[4 * i:4 * i + 4]) for i in range(nk)]
	for i in range(nk, 4 * (nr + 1)):
		temp = list(w[i - 1])
		if i % nk == 0:
			temp = temp[1:] + temp[:1]
			temp = [_SBOX[b] for b in temp]
			temp[0] ^= _RCON[i // nk - 1]
		elif nk > 6 and i % nk == 4:
			temp = [_SBOX[b] for b in temp]
		w.append([w[i - nk][j] ^ temp[j] for j in range(4)])
	return w, nr


def _add_round_key(state, w, rnd):
	for c in range(4):
		wc = w[rnd * 4 + c]
		for r in range(4):
			state[r][c] ^= wc[r]


def _sub_bytes(state):
	for r in range(4):
		for c in range(4):
			state[r][c] = _SBOX[state[r][c]]


def _shift_rows(state):
	for r in range(1, 4):
		state[r] = state[r][r:] + state[r][:r]


def _mix_columns(state):
	for c in range(4):
		a = [state[r][c] for r in range(4)]
		state[0][c] = _mul(a[0], 2) ^ _mul(a[1], 3) ^ a[2] ^ a[3]
		state[1][c] = a[0] ^ _mul(a[1], 2) ^ _mul(a[2], 3) ^ a[3]
		state[2][c] = a[0] ^ a[1] ^ _mul(a[2], 2) ^ _mul(a[3], 3)
		state[3][c] = _mul(a[0], 3) ^ a[1] ^ a[2] ^ _mul(a[3], 2)


def _encrypt_block(block, w, nr):
	state = [[block[r + 4 * c] for c in range(4)] for r in range(4)]
	_add_round_key(state, w, 0)
	for rnd in range(1, nr):
		_sub_bytes(state)
		_shift_rows(state)
		_mix_columns(state)
		_add_round_key(state, w, rnd)
	_sub_bytes(state)
	_shift_rows(state)
	_add_round_key(state, w, nr)
	return bytes(state[r][c] for c in range(4) for r in range(4))


def _ctr(key_sched, nr, icb, data):
	out = bytearray()
	ctr = bytearray(icb)
	w, nr = key_sched
	for off in range(0, len(data), 16):
		ks = _encrypt_block(ctr, w, nr)
		chunk = data[off:off + 16]
		out.extend(b ^ ks[i] for i, b in enumerate(chunk))
		for i in range(15, 11, -1):
			ctr[i] = (ctr[i] + 1) & 0xFF
			if ctr[i]:
				break
	return bytes(out)


def _ghash_mul(x, y):
	R = 0xE1 << 120
	z = 0
	v = y
	for i in range(128):
		if (x >> (127 - i)) & 1:
			z ^= v
		if v & 1:
			v = (v >> 1) ^ R
		else:
			v >>= 1
	return z


def _ghash(h, data):
	y = 0
	for off in range(0, len(data), 16):
		block = data[off:off + 16]
		block = block + b"\x00" * (16 - len(block))
		y ^= int.from_bytes(block, "big")
		y = _ghash_mul(y, h)
	return y


def decrypt(key, iv, ciphertext_and_tag, aad=b""):
	w, nr = _key_expansion(key)
	sched = (w, nr)
	tag = ciphertext_and_tag[-16:]
	ct = ciphertext_and_tag[:-16]

	h = int.from_bytes(_encrypt_block(b"\x00" * 16, w, nr), "big")

	if len(iv) == 12:
		j0 = iv + b"\x00\x00\x00\x01"
	else:
		s = _ghash(h, iv + b"\x00" * ((16 - len(iv) % 16) % 16) +
		           (len(iv) * 8).to_bytes(16, "big"))
		j0 = s.to_bytes(16, "big")

	lens = (len(aad) * 8).to_bytes(8, "big") + (len(ct) * 8).to_bytes(8, "big")
	pad_aad = aad + b"\x00" * ((16 - len(aad) % 16) % 16)
	pad_ct = ct + b"\x00" * ((16 - len(ct) % 16) % 16)
	s = _ghash(h, pad_aad + pad_ct + lens)
	ej0 = int.from_bytes(_encrypt_block(j0, w, nr), "big")
	calc_tag = (s ^ ej0).to_bytes(16, "big")
	if calc_tag != tag:
		raise ValueError("GCM tag mismatch")

	icb = bytearray(j0)
	for i in range(15, 11, -1):
		icb[i] = (icb[i] + 1) & 0xFF
		if icb[i]:
			break
	return _ctr(sched, nr, icb, ct)
