
import json as _json
import gzip
import zlib
import socket
import ssl
import io
from urllib import request as _urlreq
from urllib.error import URLError, HTTPError
from urllib.parse import urlencode, urlparse
from http.cookiejar import CookieJar




class RequestException(IOError):
	pass


class HTTPException(RequestException):
	pass


class Timeout(RequestException):
	pass


class ConnectionError(RequestException):
	pass


class exceptions:
	RequestException = RequestException
	HTTPError = HTTPException
	Timeout = Timeout
	ConnectionError = ConnectionError




class _CaseInsensitiveDict(dict):

	def __init__(self, data=None):
		super().__init__()
		self._lower = {}
		if data:
			for k, v in (data.items() if hasattr(data, "items") else data):
				self[k] = v

	def __setitem__(self, key, value):
		super().__setitem__(key, value)
		self._lower[key.lower()] = key

	def __getitem__(self, key):
		real = self._lower.get(key.lower(), key)
		return super().__getitem__(real)

	def get(self, key, default=None):
		try:
			return self[key]
		except KeyError:
			return default

	def __contains__(self, key):
		return key.lower() in self._lower


class Response:
	def __init__(self, raw_bytes, status_code, headers, url, raw_response=None):
		self._content = raw_bytes
		self.status_code = int(status_code)
		self.headers = _CaseInsensitiveDict(headers or {})
		self.url = url
		self._text = None
		self._raw_response = raw_response

	@property
	def content(self):
		if self._content is None:
			try:
				raw = self._raw_response.read() if self._raw_response else b""
				self._content = Session._decode_body(raw, self.headers)
			except (URLError, socket.timeout) as e:
				reason = getattr(e, "reason", e)
				if isinstance(reason, socket.timeout) or "timed out" in str(reason).lower():
					raise Timeout(str(reason))
				raise ConnectionError(str(reason))
			except Exception as e:
				raise RequestException(str(e))
			finally:
				self.close()
		return self._content

	def close(self):
		response, self._raw_response = self._raw_response, None
		if response is not None:
			response.close()

	def __enter__(self):
		return self

	def __exit__(self, *args):
		self.close()

	@property
	def text(self):
		if self._text is not None:
			return self._text

		charset = None
		ct = self.headers.get("content-type", "")
		if "charset=" in ct.lower():
			try:
				charset = ct.lower().split("charset=", 1)[1].split(";")[0].strip()
			except Exception:
				charset = None
		try:
			self._text = self.content.decode(charset or "utf-8", "replace")
		except (LookupError, TypeError):
			self._text = self.content.decode("utf-8", "replace")
		return self._text

	def json(self, **kwargs):
		return _json.loads(self.text, **kwargs)

	def raise_for_status(self):
		if 400 <= self.status_code < 600:
			raise HTTPException("HTTP %s for %s" % (self.status_code, self.url))




class _RedirectHandler(_urlreq.HTTPRedirectHandler):
	# Python 3.9's urllib does not handle 308, although providers use it.
	def redirect_request(self, req, fp, code, msg, headers, newurl):
		redirected = super().redirect_request(req, fp, 307 if code == 308 else code,
		                                     msg, headers, newurl)
		if redirected is not None and req.get_method() == "HEAD":
			# urllib 3.9 otherwise rebuilds this as GET and may download a video
			# when a status-only probe follows a CDN redirect.
			redirected.method = "HEAD"
		return redirected

	http_error_308 = _urlreq.HTTPRedirectHandler.http_error_302


class _NoRedirectHandler(_RedirectHandler):
	def redirect_request(self, req, fp, code, msg, headers, newurl):
		return None


class Session:
	def __init__(self):
		self.headers = {}
		self.cookies = CookieJar()


		self._opener_verify = _urlreq.build_opener(
			_urlreq.HTTPCookieProcessor(self.cookies), _RedirectHandler(),
		)
		ctx = ssl.create_default_context()
		ctx.check_hostname = False
		ctx.verify_mode = ssl.CERT_NONE
		self._opener_noverify = _urlreq.build_opener(
			_urlreq.HTTPCookieProcessor(self.cookies), _RedirectHandler(),
			_urlreq.HTTPSHandler(context=ctx),
		)
		# The redirect inspectors must see Location without opening the host page.
		# All four openers share cookies, including those set by a 302 response.
		self._opener_noredirect_verify = _urlreq.build_opener(
			_urlreq.HTTPCookieProcessor(self.cookies), _NoRedirectHandler(),
		)
		self._opener_noredirect_noverify = _urlreq.build_opener(
			_urlreq.HTTPCookieProcessor(self.cookies), _NoRedirectHandler(),
			_urlreq.HTTPSHandler(context=ctx),
		)

	def close(self):
		pass



	@staticmethod
	def _encode_body(data, json_obj, headers):
		if json_obj is not None:
			body = _json.dumps(json_obj).encode("utf-8")
			headers.setdefault("Content-Type", "application/json")
			return body
		if data is None:
			return None
		if isinstance(data, (bytes, bytearray)):
			return bytes(data)
		if isinstance(data, str):
			return data.encode("utf-8")
		if isinstance(data, dict):
			headers.setdefault("Content-Type",
			                   "application/x-www-form-urlencoded")
			return urlencode(data).encode("utf-8")
		raise TypeError("Unsupported data type: %r" % type(data))

	@staticmethod
	def _decode_body(raw_bytes, response_headers, max_bytes=None):
		enc = (response_headers.get("Content-Encoding")
		       or response_headers.get("content-encoding") or "").lower()
		if not raw_bytes or max_bytes == 0:
			return b""
		try:
			if max_bytes is not None:
				# Some servers ignore Accept-Encoding: identity. Bound decoded data
				# as well as the wire read, instead of expanding it all in memory.
				if "gzip" in enc:
					return zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(raw_bytes, max_bytes)
				if "deflate" in enc:
					try:
						return zlib.decompressobj().decompress(raw_bytes, max_bytes)
					except zlib.error:
						return zlib.decompressobj(-zlib.MAX_WBITS).decompress(raw_bytes, max_bytes)
			if "gzip" in enc:
				return gzip.decompress(raw_bytes)
			if "deflate" in enc:
				try:
					return zlib.decompress(raw_bytes)
				except zlib.error:
					return zlib.decompress(raw_bytes, -zlib.MAX_WBITS)
		except Exception:
			pass
		return raw_bytes



	def request(self, method, url, headers=None, params=None, data=None,
	            json=None, timeout=None, stream=False, allow_redirects=True,
	            verify=True, max_bytes=None, **_ignored):

		if params:
			qs = urlencode(params, doseq=True)
			sep = "&" if ("?" in url) else "?"
			url = url + sep + qs


		hdrs = dict(self.headers)
		if headers:
			for k, v in headers.items():
				hdrs[str(k)] = str(v)


		if not any(k.lower() == "accept-encoding" for k in hdrs):
			hdrs["Accept-Encoding"] = "identity" if max_bytes is not None else "gzip, deflate"

		body = self._encode_body(data, json, hdrs)

		req = _urlreq.Request(url, data=body, method=method.upper())
		for k, v in hdrs.items():
			req.add_header(k, v)

		if allow_redirects:
			opener = self._opener_verify if verify else self._opener_noverify
		else:
			opener = self._opener_noredirect_verify if verify else self._opener_noredirect_noverify

		try:
			to = timeout if timeout is not None else socket._GLOBAL_DEFAULT_TIMEOUT
			resp = opener.open(req, timeout=to)
		except HTTPError as e:
			resp = e
		except (URLError, socket.timeout) as e:
			reason = getattr(e, "reason", e)
			if isinstance(reason, socket.timeout) or "timed out" in str(reason).lower():
				raise Timeout(str(reason))
			raise ConnectionError(str(reason))
		except Exception as e:
			raise RequestException(str(e))

		final_url = resp.geturl()
		resp_headers = dict(resp.headers.items()) if resp.headers else {}
		response = Response(None, resp.code, resp_headers, final_url, raw_response=resp)
		if max_bytes is not None:
			try:
				limit = max(0, int(max_bytes))
				raw = resp.read(limit)
				response._content = self._decode_body(raw, resp_headers, limit)
			except (URLError, socket.timeout) as e:
				reason = getattr(e, "reason", e)
				if isinstance(reason, socket.timeout) or "timed out" in str(reason).lower():
					raise Timeout(str(reason))
				raise ConnectionError(str(reason))
			except Exception as e:
				raise RequestException(str(e))
			finally:
				response.close()
		elif not stream:
			response.content
		return response


	def get(self, url, **kw): return self.request("GET", url, **kw)
	def post(self, url, **kw): return self.request("POST", url, **kw)
	def head(self, url, **kw): return self.request("HEAD", url, **kw)



RequestException = RequestException

__streamhub_vendored__ = True
