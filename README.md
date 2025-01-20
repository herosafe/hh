# OA 协作平台

这是一个基于Python Flask的在线协作平台，主要功能包括：

## 功能特性
- 用户认证（登录/注册）
- 文件上传与管理
- 实时聊天功能
- 协作文档编辑
- 文件版本管理
- 公告系统

## 公告功能
- 管理员可以发布和管理公告
- 普通用户可以查看所有公告
- 首页显示最新5条公告
- 公告轮播自动调整播放速度
- 管理员访问路径：/admin/announcements
- 普通用户访问路径：/announcements

## 最近更新
- 新增公告系统
- 优化用户权限管理
- 改进导航栏逻辑
- 添加公告轮播功能

## 运行要求
- Python 3.8+
- Flask 2.0+
- SQLite3

## 安装与运行
1. 安装依赖：
   ```bash
   pip install -r requirements.txt
   ```
2. 初始化数据库：
   ```bash
   python create_db.py
   ```
3. 启动应用：
   ```bash
   python app.py
   ```
4. 访问 http://localhost:5000

## 项目结构
```
.
├── app.py                # 主应用文件
├── create_db.py          # 数据库初始化
├── requirements.txt      # 依赖文件
├── instance/             # 数据库文件
├── templates/            # HTML模板
├── uploads/              # 上传文件
└── chat_logs/            # 聊天记录
