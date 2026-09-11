/*!
 * unity-loader.js — WebGL bootstrapper with client-side part reassembly.
 *
 * The Unity build is chopped into <100 MB chunks (see tools/split_package.py)
 * so it can live on GitHub, which hard-rejects any single file over 100 MB.
 * This loader downloads every chunk, stitches the pieces back together into a
 * Blob and hands Unity object URLs, so the engine sees one normal file.
 *
 * Protocol: call  window.GTW.boot({ onStage, onProgress })  -> Promise<void>
 */
(function (global) {
  'use strict';

  var MANIFEST = 'build.json';
  var USE_SW = 'serviceWorker' in navigator && location.protocol !== 'file:';
  var VFS_ROOT = '__vfs__/';

  function log() {
    try { console.log.apply(console, ['[GTW]'].concat([].slice.call(arguments))); } catch (e) {}
  }

  /** Stream a URL to a Blob while reporting the byte count. */
  function fetchBlob(url, onBytes, signal) {
    return fetch(url, { signal: signal, cache: 'force-cache' })
      .then(function (res) {
        if (!res.ok) throw new Error('HTTP ' + res.status + ' — ' + url);
        var type = res.headers.get('Content-Type') || 'application/octet-stream';
        var total = parseInt(res.headers.get('Content-Length') || '0', 10);
        if (!res.body || !res.body.getReader) {
          return res.blob().then(function (b) { onBytes && onBytes(b.size); return b; });
        }
        var reader = res.body.getReader();
        var chunks = [];
        return (function pump() {
          return reader.read().then(function (r) {
            if (r.done) {
              var blob = new Blob(chunks, { type: type });
              if (total && blob.size !== total) {
                log('size mismatch for', url, blob.size, '!=', total);
              }
              return blob;
            }
            chunks.push(r.value);
            onBytes && onBytes(r.value.length);
            return pump();
          });
        })();
      });
  }

  /** Download all parts of one file and return a single Blob. */
  function assembleFile(entry, onBytes, onStage) {
    var parts = entry.parts || [];
    if (parts.length <= 1) {
      // Single file: let Unity fetch it straight from the CDN (no need to buffer).
      return Promise.resolve(null);
    }
    var pieces = [];
    var done = 0;
    return parts.reduce(function (chain, p) {
      return chain.then(function () {
        done++;
        onStage && onStage('正在下载分片 ' + done + '/' + parts.length + ' · ' + shortName(entry.path));
        return fetchBlob(p.url, onBytes);
      }).then(function (blob) { pieces.push(blob); });
    }, Promise.resolve()).then(function () {
      return new Blob(pieces, { type: entry.type || 'application/octet-stream' });
    });
  }

  function shortName(p) { return String(p).split('/').pop(); }

  function emit(type, payload) {
    try {
      var msg = payload || {};
      msg.type = type;
      if (global.parent && global.parent !== global) global.parent.postMessage(msg, '*');
    } catch (e) {}
  }

  /** Map manifest paths -> browser-fetchable URLs (direct file or stitched Blob). */
  function prepareAssets(manifest, onStage, onProgress) {
    var files = manifest.files || [];
    var totalBytes = files.reduce(function (n, f) {
      return n + (f.parts && f.parts.length > 1 ? f.size : 0);
    }, 0);
    var loaded = 0;
    var map = {};
    var blobUrls = [];

    // Small / single-file assets are served straight from the site.
    files.forEach(function (f) {
      if (!f.parts || f.parts.length <= 1) map[f.path] = f.path;
    });

    var oversized = files.filter(function (f) {
      return f.parts && f.parts.length > 1 && f.size > 1.8e9;
    });
    if (oversized.length) {
      log('警告: 以下文件超过浏览器 Blob 上限，可能失败:', oversized.map(function (f) { return f.path; }));
    }

    var queue = files.filter(function (f) { return f.parts && f.parts.length > 1; });

    return queue.reduce(function (chain, f) {
      return chain.then(function () {
        return assembleFile(f, function (n) {
          loaded += n;
          if (totalBytes) onProgress && onProgress(Math.min(0.85, 0.85 * loaded / totalBytes));
        }, onStage).then(function (blob) {
          if (blob) {
            var url = URL.createObjectURL(blob);
            blobUrls.push(url);
            map[f.path] = url;            // blob: URLs are same-origin and streamable
          } else {
            map[f.path] = f.path;
          }
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

      return fetch(MANIFEST, { cache: 'no-store' })
        .then(function (r) {
          if (!r.ok) throw new Error('no-manifest');
          return r.json();
        })
        .then(function (manifest) {
          if (!manifest || manifest.mode !== 'webgl' || !manifest.unity) {
            throw new Error('bad-manifest');
          }
          var u = manifest.unity;
          onStage('准备下载游戏资源…');
          return prepareAssets(manifest, onStage, onProgress).then(function (prep) {
            state.blobUrls = prep.blobUrls;
            var cfg = {
              dataUrl: prep.map[u.dataUrl] || u.dataUrl,
              frameworkUrl: prep.map[u.frameworkUrl] || u.frameworkUrl,
              codeUrl: prep.map[u.codeUrl] || u.codeUrl,
              companyName: u.companyName || '',
              productName: u.productName || '',
              productVersion: u.productVersion || '1.0',
              // Unity 6 requires this block to exist.
              streamingAssetsUrl: u.streamingAssetsUrl || 'StreamingAssets',
              matchWebGLToCanvasSize: true,
              devicePixelRatio: Math.min(window.devicePixelRatio || 1, 2)
            };
            onStage('启动 Unity 引擎…');
            emit('unity-booting');
            return injectLoader(prep.map[u.loaderUrl] || u.loaderUrl).then(function () {
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

    /** Free the stitched Blobs once the engine has them in memory. */
    releaseBlobs: function () {
      state.blobUrls.forEach(function (u) { try { URL.revokeObjectURL(u); } catch (e) {} });
      state.blobUrls = [];
    }
  };
})(typeof window !== 'undefined' ? window : this);
