return {
  "okuuva/auto-save.nvim",
  event = { "InsertLeave", "TextChanged" },
  opts = {
    debounce_delay = 1000, -- 1 second after last change
    condition = function(buf)
      -- Only save normal file buffers with a filename
      local buftype = vim.bo[buf].buftype
      local filename = vim.api.nvim_buf_get_name(buf)
      return buftype == "" and filename ~= ""
    end,
  },
}
