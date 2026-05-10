const { exec } = require('node:child_process')
const path = require('node:path')
const express = require('express')
const packageJSON = require('../package.json')
const getModulesDefinitions = require('../routes/router')
const request = require('../utils/request')
const decode = require('safe-decode-uri-component')
const cache = require('../utils/cache').middleware
const { cookieToJson } = require('../utils/tools')
const fileUpload = require('express-fileupload')

const VERSION_CHECK_RESULT = {
  FAILED: -1,
  NOT_LATEST: 0,
  LATEST: 1,
}
const checkVersion = () => {
  return new Promise((resolve) => {
    exec('npm info NeteaseCloudMusicApi version', (err, stdout) => {
      if (!err) {
        let version = stdout.trim()
        let status =
          packageJSON.version < version
            ? VERSION_CHECK_RESULT.NOT_LATEST
            : VERSION_CHECK_RESULT.LATEST
        resolve({
          status,
          curVersion: packageJSON.version,
          latestVersion: version,
        })
      }
      resolve({
        status: VERSION_CHECK_RESULT.FAILED,
      })
    })
  })
}

const constructServer = async (moduleDefs) => {
  const app = express()
  // console.log(process.env)
  const { CORS_ALLOW_ORIGIN } = process.env

  app.set('trust proxy', true)

  /**
   * app.use() : 路由和中间件
   */

  /**
   * CORS & Preflight request
   */
  app.use((req, res, next) => {
    if (req.path !== '/' && !req.path.includes('.')) {
      res.set({
        'Access-Control-Allow-Credentials': true,
        'Access-Control-Allow-Origin':
          CORS_ALLOW_ORIGIN || req.headers.origin || '*',
        'Access-Control-Allow-Headers': 'X-Requested-With,Content-Type',
        'Access-Control-Allow-Methods': 'PUT,POST,GET,DELETE,OPTIONS',
        'Content-Type': 'application/json; charset=utf-8',
      })
    }
    req.method === 'OPTIONS' ? res.status(204).end() : next()
  })

  /**
   * Cookie Parser
   */
  app.use((req, _, next) => {
    req.cookies = {}
    //;(req.headers.cookie || '').split(/\s*;\s*/).forEach((pair) => { //  Polynomial regular expression //
    // ;\s+ 分号空格  (?<!\s)
    // (?<!y)x 后行否定断言 ：x 不跟随 y 时匹配 x（yx 不匹配）  (?<!\s)\s+：多个空格前面没有空格匹配空格
    ;(req.headers.cookie || '').split(/;\s+|(?<!\s)\s+$/g).forEach((pair) => {
      let crack = pair.indexOf('=')
      if (crack < 1 || crack == pair.length - 1) return
      req.cookies[decode(pair.slice(0, crack)).trim()] = decode(
        pair.slice(crack + 1),
      ).trim()
    })
    next()
  })

  /**
   * Body Parser and File Upload
   */
  app.use(express.json()) //body中json解析
  app.use(express.urlencoded({ extended: false })) //This option allows to choose between parsing the URL-encoded data with the querystring library
  app.use(fileUpload()) //文件上传

  /**
   * Serving static files
   */
  app.use(express.static(path.join(__dirname, '..', 'public')))

  /**
   * Cache
   */
  app.use(
    cache(
      '2 minutes',
      (req, res) =>
        req.method === 'GET' &&
        !req.headers['x-apicache-bypass'] &&
        !req.headers['x-apicache-force-fetch'] &&
        res.statusCode === 200,
    ),
  )

  const special = {
    'daily_signin.js': '/daily_signin',
    'fm_trash.js': '/fm_trash',
    'personal_fm.js': '/personal_fm',
  }
  const moduleDefinitions =
    moduleDefs ||
    (await getModulesDefinitions(path.join(__dirname, '..', 'routes/module')))

  for (const moduleDef of moduleDefinitions) {
    //Register the route
    app.use(moduleDef.route, async (req, res) => {
      ;[req.query, req.body].forEach((item) => {
        if (typeof item.cookie === 'string') {
          item.cookie = cookieToJson(decode(item.cookie))
        }
      })
      let query = Object.assign(
        {},
        { cookie: req.cookies },
        req.query,
        req.body,
        req.files,
      )
      let moduleResponse = null
      console.log('query', query)
      try {
        moduleResponse = await moduleDef.module(query, (...params) => {
          //参数注入客户端IP
          const obj = [...params]
          let ip = req.ip

          if (ip.substring(0, 7) == '::ffff:') {
            ip = ip.substring(7)
          }
          obj[3] = {
            ...obj[3],
            ip,
          }
          return request(...obj)
        })
        console.log('[OK]', decode(req.originalUrl))
        const cookies = moduleResponse.cookie
        if (Array.isArray(cookies) && cookies.length > 0) {
          if (req.protocol === 'https') {
            // Try to fix CORS SameSite Problem
            // Appends the specified value to the HTTP response header field
            res.append(
              'Set-Cookie',
              cookies.map((cookie) => {
                return cookie + '; SameSite=None; Secure'
              }),
            )
          } else {
            res.append('Set-Cookie', cookies)
          }
        }
        res.status(moduleResponse.status).send(moduleResponse.body)
      } catch (error) {
        console.log('[ERR]', decode(req.originalUrl), {
          status: moduleResponse && moduleResponse.status,
          body: moduleResponse && moduleResponse.body,
          error: error && error.message,
        })
        if (!moduleResponse || !moduleResponse.body) {
          res.status(404).send({
            code: 402,
            data: null,
            msg: 'Not Found',
          })
          return
        }
        if (moduleResponse.body.code == '301')
          moduleResponse.body.msg = '需要登录'
        if (moduleResponse.cookie) {
          res.append('Set-Cookie', moduleResponse.cookie)
        }
        res.status(moduleResponse.status).send(moduleResponse.body)
      }
    })
  }

  app.get('/hello', (req, res) => {
    res.send('Hello World!')
  })

  return app
}
const apiServe = async (options = {}) => {
  const port = Number(options.port || process.env.PORT || '3001')
  const host = (options.host || process.env.HOST || '').trim()

  const app = await constructServer(options.moduleDefs)

  // 版本检查走网络/子进程，勿阻塞 listen；否则 GUI 健康检查易超时（尤其 npm/registry 慢或不可达时）
  void checkVersion().then(({ status, curVersion, latestVersion }) => {
    if (status == VERSION_CHECK_RESULT.NOT_LATEST) {
      // console.log(`最新版本: ${latestVersion}, 当前版本: ${curVersion}, 请及时更新`);
    }
  })

  const appExt = app
  const onListen = () => {
    const shownHost = host || 'localhost'
    console.log(`server running @ http://${shownHost}:${port}`)
  }
  appExt.server = host
    ? app.listen(port, host, onListen)
    : app.listen(port, onListen)

  // use to test
  return appExt
}

module.exports = {
  apiServe,
}
