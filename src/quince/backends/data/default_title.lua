--[[
Quince: an EPUB needs a title. Use the document's own title, else the file name that Quince
passes as the metadata field `quince-title`.
]]

function Meta(meta)
  if meta.title == nil and meta["quince-title"] ~= nil then
    meta.title = meta["quince-title"]
  end
  meta["quince-title"] = nil
  return meta
end
