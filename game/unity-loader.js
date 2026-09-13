/*!
 * unity-loader.js — WebGL bootstrapper with client-side part reassembly.
 *
 * The Unity build is chopped into <100 MB chunks (see tools/split_package.py)
 * so it can live on GitHub, which hard-rejects any single file over 100 MB.
 * This loader:
 *
 *   1. pre-assembles the four core artefacts (.loader.js / .data / .framework.js
 *      / .wasm) into Blob URLs and hands those to createUnityInstance;
 *   2. installs a fetch + XMLHttpRequest interceptor so that when the running
 *      game asks for a StreamingAssets file (Addressables bundles, FMOD banks,
 *      …) that was split into parts, we transparently download the parts,
 *      stitch them and answer with the whole thing.
 *
 * Step 2 is lazy on purpose: the Addressables content is well over a gigabyte,
 * so it must NOT be downloaded up front — only the level the player actually
 * loads gets pulled in.
 *
 * Protocol: call  window.GTW.boot({ canvas, onStage, onProgress })  -> Promise
 */
(function (global) {
  'use strict';

  var MANIFEST = 'build.json';

  // Where the *parts* live.  The shell (index.html / this file / build.json)
  // is tiny and hosted on GitHub Pages, but the payload is ~2 GB and way past
  // the Pages 1 GB site cap — so it is served straight off
  // raw.githubusercontent.com, which is the only GitHub endpoint that sends
  // `Access-Control-Allow-Origin: *` (Release assets do NOT, so fetch() to a
  // release URL dies with a CORS error even though curl works fine).
  // Empty string => same origin (everything in one repo).
  var DATA_BASE = 'https://raw.githubusercontent.com/daxuanba/daxuanba-67.github.io/data/';

  // path -> entry, only for files that were split (parts.length > 1)
  var partMap = {};
  var blobCache = {};     // path -> Promise<Blob>

  /** Re-root a manifest-relative url onto the data host when one is configured. */
  function resolveUrl(u) {
    if (!DATA_BASE || /^https?:/i.test(u) || /^blob:/i.test(u)) return u;
    return DATA_BASE + String(u).replace(/^\/+/, '');
  }

  function log() {
    try { console.log.apply(console, ['[GTW]'].concat([].slice.call(arguments))); } catch (e) {}
  }

  /* ------------------------------------------------------------------ utils */

  /** Normalise any URL (absolute / relative / ./x) into "path under site root". */
  function normalise(url) {
    try {
      var base = (global.document && global.document.baseURI) || global.location.href;
      var abs = new URL(String(url), base);
      var p = abs.pathname;
      // strip the leading "/" so it matches manifest paths ("build/...")
      return decodeURIComponent(p.replace(/^\/+/, ''));
    } catch (e) {
      return String(url).replace(/^\.?\//, '');
    }
  }

  function matchPart(url) {
    if (!url) return null;
    var key = normalise(url);
    if (partMap[key]) return partMap[key];
    // tolerate a leading "./" or a fully-qualified duplicate
    var alt = key.replace(/^\.?\//, '');
    if (partMap[alt]) return partMap[alt];
    // The site is served from a sub-path (…/daxuanba-67.github.io/game/), so a
    // request for "build/StreamingAssets/aa/x.bundle" normalises to
    // "daxuanba-67.github.io/game/build/StreamingAssets/aa/x.bundle" and an
    // exact match fails.  Fall back to a path-suffix match.
    for (var k in partMap) {
      if (key.length > k.length && key.slice(-(k.length + 1)) === '/' + k) return partMap[k];
    }
    return null;
  }

  function shortName(p) { return String(p).split('/').pop(); }

  /* --------------------------------------------------------------- fetching */

  var origFetch = global.fetch ? global.fetch.bind(global) : null;

  /** Stream a URL to a Blob while reporting the byte count. */
  function fetchBlob(url, onBytes) {
    return origFetch(url, { cache: 'force-cache' }).then(function (res) {
      if (!res.ok) throw new Error('HTTP ' + res.status + ' — ' + url);
      var type = res.headers.get('Content-Type') || 'application/octet-stream';
      if (!res.body || !res.body.getReader) {
        return res.blob().then(function (b) { onBytes && onBytes(b.size); return b; });
      }
      var reader = res.body.getReader();
      var chunks = [];
      return (function pump() {
        return reader.read().then(function (r) {
          if (r.done) return new Blob(chunks, { type: type });
          chunks.push(r.value);
          onBytes && onBytes(r.value.length);
          return pump();
        });
      })();
    });
  }

  /** Download every part of a manifest entry (in order) and return one Blob. */
  function assemble(entry, onBytes) {
    var parts = entry.parts || [];
    if (parts.length === 1) {
      return fetchBlob(resolveUrl(parts[0].url), onBytes);
    }
    var pieces = [];
    return parts.reduce(function (chain, p) {
      return chain.then(function () {
        return fetchBlob(resolveUrl(p.url), onBytes);
      }).then(function (blob) { pieces.push(blob); });
    }, Promise.resolve()).then(function () {
      return new Blob(pieces, { type: entry.type || 'application/octet-stream' });
    });
  }

  /** assemble() with a cache, so a file is never downloaded twice. */
  function assembleCached(entry, onBytes) {
    var key = entry.path;
    if (!blobCache[key]) {
      blobCache[key] = assemble(entry, onBytes).catch(function (e) {
        delete blobCache[key];
        throw e;
      });
    }
    return blobCache[key];
  }

  /* ---------------------------------------------------- request interceptors */

  function installFetchInterceptor() {
    if (!origFetch) return;
    global.fetch = function (input, init) {
      var url = typeof input === 'string' ? input : (input && input.url);
      var entry = matchPart(url);
      if (!entry) return origFetch(input, init);
      log('intercept fetch ->', entry.path);
      return assembleCached(entry).then(function (blob) {
        return new Response(blob, {
          status: 200,
          statusText: 'OK',
          headers: {
            'Content-Type': entry.type || 'application/octet-stream',
            'Content-Length': String(blob.size),
          },
        });
      });
    };
  }

  function installXhrInterceptor() {
    var NativeXHR = global.XMLHttpRequest;
    if (!NativeXHR) return;

    function define(obj, prop, value) {
      try {
        Object.defineProperty(obj, prop, { value: value, writable: true, configurable: true });
      } catch (e) { /* ignore */ }
    }

    function PatchedXHR() {
      var xhr = new NativeXHR();
      var entry = null;

      // Mirror the handful of properties your average caller reads.
      var open = xhr.open;
      var send = xhr.send;

      xhr.open = function (method, url) {
        entry = matchPart(url);
        if (!entry) { open.apply(xhr, arguments); }
        else { xhr.__url = url; }
      };

      xhr.send = function (body) {
        if (!entry) return send.apply(xhr, arguments);
        var self = xhr;
        var url = self.__url;
        log('intercept xhr ->', entry.path);

        assembleCached(entry, function (n) {
          var pe;
          try {
            pe = new ProgressEvent('progress', {
              lengthComputable: true, loaded: n, total: entry.size,
            });
          } catch (e) {
            pe = null;
          }
          try {
            if (typeof self.onprogress === 'function') {
              self.onprogress(pe || { lengthComputable: true, loaded: n, total: entry.size, target: self });
            }
            if (pe && typeof self.dispatchEvent === 'function') self.dispatchEvent(pe);
          } catch (e) {}
        }).then(function (blob) {
          var want = self.responseType;
          var finish = function (payload) {
            define(self, 'readyState', 4);
            define(self, 'status', 200);
            define(self, 'statusText', 'OK');
            define(self, 'responseURL', url);
            if (want === 'blob') {
              define(self, 'response', payload.blob);
            } else if (want === 'arraybuffer') {
              define(self, 'response', payload.buffer);
            } else if (want === 'json') {
              define(self, 'response', JSON.parse(payload.text));
            } else {
              define(self, 'response', payload.text);
              define(self, 'responseText', payload.text);
            }
            var ev = new Event('readystatechange');
            if (typeof self.onreadystatechange === 'function') self.onreadystatechange(ev);
            if (typeof self.dispatchEvent === 'function') self.dispatchEvent(ev);
            var le = new Event('load');
            if (typeof self.onload === 'function') self.onload(le);
            if (typeof self.dispatchEvent === 'function') self.dispatchEvent(le);
            if (typeof self.onloadend === 'function') self.onloadend(new Event('loadend'));
          };
          if (want === 'arraybuffer') {
            blob.arrayBuffer().then(function (buffer) { finish({ blob: blob, buffer: buffer }); });
          } else if (want === 'blob') {
            finish({ blob: blob });
          } else {
            blob.text().then(function (text) { finish({ blob: blob, text: text }); });
          }
        }).catch(function (err) {
          log('xhr intercept failed', entry.path, err);
          define(self, 'readyState', 4);
          define(self, 'status', 0);
          var ev = new Event('error');
          if (typeof self.onerror === 'function') self.onerror(ev);
          if (typeof self.dispatchEvent === 'function') self.dispatchEvent(ev);
        });
      };

      // expose the native header helpers so callers don't crash
      xhr.getResponseHeader = function () { return null; };
      xhr.getAllResponseHeaders = function () { return ''; };
      return xhr;
    }

    PatchedXHR.prototype = NativeXHR.prototype;
    try {
      global.XMLHttpRequest = PatchedXHR;
      // keep the static constants reachable
      ['UNSENT', 'OPENED', 'HEADERS_RECEIVED', 'LOADING', 'DONE'].forEach(function (k) {
        if (NativeXHR[k] !== undefined) global.XMLHttpRequest[k] = NativeXHR[k];
      });
    } catch (e) {
      log('could not patch XMLHttpRequest', e);
    }
  }

  /* ------------------------------------------------------------ boot sequence */

  function emit(type, payload) {
    try {
      var msg = payload || {};
      msg.type = type;
      if (global.parent && global.parent !== global) global.parent.postMessage(msg, '*');
    } catch (e) {}
  }

  /** Pre-assemble the four core artefacts (they are needed immediately). */
  function prepareCore(manifest, onStage, onProgress) {
    var files = (manifest.files || []).filter(function (f) {
      return f.parts && f.parts.length > 1 && !/^build\/StreamingAssets\//.test(f.path);
    });
    var totalBytes = files.reduce(function (n, f) { return n + f.size; }, 0);
    var loaded = 0;
    var map = {};
    var blobUrls = [];

    return files.reduce(function (chain, f) {
      return chain.then(function () {
        onStage && onStage('正在下载 ' + shortName(f.path) + ' …');
        return assemble(f, function (n) {
          loaded += n;
          if (totalBytes) onProgress && onProgress(Math.min(0.85, 0.85 * loaded / totalBytes));
        }).then(function (blob) {
          var url = URL.createObjectURL(blob);
          blobUrls.push(url);
          map[f.path] = url;
        });
      });
    }, Promise.resolve()).then(function () {
      return { map: map, blobUrls: blobUrls };
    });
  }

  function injectLoader(url) {
    return new Promise(function (resolve, reject) {
      var s = document.createElement('script');
      s.src = url;
      s.onload = resolve;
      s.onerror = function () { reject(new Error('无法加载 Unity loader: ' + url)); };
      document.body.appendChild(s);
    });
  }

  var state = { instance: null, blobUrls: [], running: false };

  global.GTW = {
    state: state,

    /** Boot Unity.  opts: { canvas, onStage, onProgress } */
    boot: function (opts) {
      opts = opts || {};
      var onStage = opts.onStage || function () {};
      var onProgress = opts.onProgress || function () {};

      if (state.running) return Promise.resolve();
      state.running = true;

      return (origFetch ? origFetch(MANIFEST, { cache: 'no-store' }) : Promise.reject(new Error('fetch unsupported')))
        .then(function (r) {
          if (!r.ok) throw new Error('no-manifest');
          return r.json();
        })
        .then(function (manifest) {
          if (!manifest || manifest.mode !== 'webgl' || !manifest.unity) {
            throw new Error('bad-manifest');
          }
          var u = manifest.unity;

          // Data host precedence: explicit override > build.json > same origin.
          if (global.GTW_DATA_BASE !== undefined) {
            DATA_BASE = global.GTW_DATA_BASE || '';
          } else if (manifest.dataBase) {
            DATA_BASE = manifest.dataBase;
          }
          if (DATA_BASE && !/\/$/.test(DATA_BASE)) DATA_BASE += '/';
          if (DATA_BASE) log('数据源:', DATA_BASE);

          // Index every manifest file so the interceptors can find them.
          // (Single-part files are indexed too, otherwise they would be fetched
          //  from the Pages origin instead of the configured data host.)
          (manifest.files || []).forEach(function (f) {
            if (f.parts && f.parts.length) partMap[normalise(f.path)] = f;
          });
          installFetchInterceptor();
          installXhrInterceptor();

          onStage('准备下载游戏资源…');
          return prepareCore(manifest, onStage, onProgress).then(function (prep) {
          state.blobUrls = prep.blobUrls;
          // A core artefact that was NOT split lives on the data host too.
          var coreUrl = function (p) { return prep.map[p] || (DATA_BASE ? DATA_BASE + p : p); };
          var cfg = {
            dataUrl: coreUrl(u.dataUrl),
            frameworkUrl: coreUrl(u.frameworkUrl),
            codeUrl: coreUrl(u.codeUrl),
            companyName: u.companyName || '',
            productName: u.productName || '',
            productVersion: u.productVersion || '1.0',
            // Unity 6 requires this block to exist.
            streamingAssetsUrl: u.streamingAssetsUrl || 'StreamingAssets',
            matchWebGLToCanvasSize: true,
            devicePixelRatio: Math.min(window.devicePixelRatio || 1, 2),
          };
          onStage('启动 Unity 引擎…');
          emit('unity-booting');
          return injectLoader(coreUrl(u.loaderUrl)).then(function () {
              if (typeof global.createUnityInstance !== 'function') {
                throw new Error('createUnityInstance 不可用(loader 未正确加载)');
              }
              return global.createUnityInstance(opts.canvas, cfg, function (v) {
                onStage('引擎加载中…');
                onProgress(0.85 + 0.15 * v);
                emit('unity-progress', { value: 0.85 + 0.15 * v });
              });
            });
          });
        })
        .then(function (inst) {
          state.instance = inst;
          onProgress(1);
          emit('unity-progress', { value: 1 });
          emit('unity-ready');
          return inst;
        })
        .catch(function (err) {
          state.running = false;
          log('boot failed:', err);
          throw err;
        });
    },

    /** Free the stitched core Blobs once the engine has them in memory. */
    releaseBlobs: function () {
      state.blobUrls.forEach(function (u) { try { URL.revokeObjectURL(u); } catch (e) {} });
      state.blobUrls = [];
    },
  };
})(typeof window !== 'undefined' ? window : this);
