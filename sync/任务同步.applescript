on run
	set homePath to POSIX path of (path to home folder)
	set scriptPath to homePath & ".wb-sync/sync.py"
	try
		set res to do shell script "/usr/bin/python3 " & quoted form of scriptPath
		if res is "" then set res to "完成"
		display dialog "已同步：" & res with title "任务看板" buttons {"好"} default button 1 giving up after 4
	on error errMsg number errNum
		if errNum is -128 then return
		display dialog "同步失败" & return & return & errMsg with title "任务看板" buttons {"好"} default button 1
	end try
end run
