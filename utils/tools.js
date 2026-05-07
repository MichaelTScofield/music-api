module.exports = {
  toBoolean(val) {
    if (typeof val === 'boolean') return val
    if (val === '') return val
    return val === 'true' || val == '1'
  },

  cookieToJson(cookie) {
    if (!cookie) return {}

    let cookieArr = cookie.split(';')
    let obj = {}

    cookieArr.forEach((element) => {
      let arr = element.split('=')

      if (arr.length >= 2) {
        let key = arr[0].trim()
        let value = arr.slice(1).join('=').trim() // 防止 value 里有 =
        obj[key] = value
      }
    })

    return obj
  },

  getRandom(...params) {
    let random // ✅ 修复未声明问题

    if (params.length > 2) return

    if (params.length === 2) {
      let [start, end] = params
      if (start < end && start >= 0) {
        let gap = end - start
        random = Math.floor(start + Math.random() * gap)
      }
    }

    if (params.length === 1) {
      let [end] = params
      random = Math.floor(Math.random() * end)
    }

    return random
  },
}
