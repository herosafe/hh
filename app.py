from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_socketio import SocketIO, send, join_room, leave_room
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import os
from datetime import datetime

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///database.db'
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['ALLOWED_EXTENSIONS'] = {'txt', 'pdf', 'png', 'jpg', 'jpeg', 'gif', 'doc', 'docx', 'xls', 'xlsx'}

db = SQLAlchemy(app)
online_users = {}
socketio = SocketIO(app, 
    cors_allowed_origins="*", 
    logger=True, 
    engineio_logger=True,
    ping_timeout=60,
    ping_interval=25,
    max_http_buffer_size=1e8
)

# 数据库模型
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(120), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    is_approved = db.Column(db.Boolean, default=False)
    factory = db.Column(db.String(50))
    department = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class SharedFile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    original_name = db.Column(db.String(255), nullable=False)
    allow_edit = db.Column(db.Boolean, default=False)
    allow_view = db.Column(db.Boolean, default=True)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    user = db.relationship('User', backref='files')
    active_editors = db.Column(db.Integer, default=0)

class ChatMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    user = db.relationship('User', backref='messages')
    room = db.Column(db.String(100), default='global')

# 初始化数据库
with app.app_context():
    db.create_all()

# 初始化Flask-Login
login_manager = LoginManager()
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

login_manager.init_app(app)

# 文件上传相关函数
def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

# 协同编辑相关路由
@app.route('/collaborative_edit')
@login_required
def collaborative_edit():
    files = SharedFile.query.order_by(SharedFile.uploaded_at.desc()).all()
    return render_template('collaborative_edit.html', files=files)

@app.route('/upload', methods=['POST'])
@login_required
def upload_file():
    if 'file' not in request.files:
        flash('没有选择文件')
        return redirect(url_for('collaborative_edit'))
    
    file = request.files['file']
    if file.filename == '':
        flash('没有选择文件')
        return redirect(url_for('collaborative_edit'))
    
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        unique_filename = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
        file.save(file_path)
        
        new_file = SharedFile(
            filename=unique_filename,
            original_name=filename,
            allow_view='allow_view' in request.form,
            allow_edit='allow_edit' in request.form,
            user_id=current_user.id
        )
        db.session.add(new_file)
        db.session.commit()
        
        flash('文件上传成功')
        return redirect(url_for('collaborative_edit'))
    
    flash('不支持的文件类型')
    return redirect(url_for('collaborative_edit'))

@app.route('/download/<int:file_id>')
@login_required
def download_file(file_id):
    file = SharedFile.query.get_or_404(file_id)
    return send_from_directory(
        app.config['UPLOAD_FOLDER'],
        file.filename,
        as_attachment=True,
        download_name=file.original_name
    )

@app.route('/edit/<int:file_id>')
@login_required
def edit_file(file_id):
    file = SharedFile.query.get_or_404(file_id)
    if not file.allow_view and file.user_id != current_user.id:
        flash('没有权限查看该文件')
        return redirect(url_for('collaborative_edit'))
    
    try:
        with open(os.path.join(app.config['UPLOAD_FOLDER'], file.filename), 'r', encoding='utf-8') as f:
            content = f.read()
        return render_template('edit_file.html', file=file, content=content)
    except Exception as e:
        flash('无法打开文件')
        return redirect(url_for('collaborative_edit'))

@socketio.on('join_edit')
def handle_join_edit(data):
    file_id = data['file_id']
    file = SharedFile.query.get(file_id)
    
    if file and (file.allow_edit or current_user.id == file.user_id):
        if file.active_editors < 5:
            file.active_editors += 1
            db.session.commit()
            join_room(f'edit_{file_id}')
            send({
                'type': 'system',
                'message': f'{current_user.email} 加入了编辑'
            }, room=f'edit_{file_id}')
        else:
            send({
                'type': 'error',
                'message': '已达到最大同时编辑人数'
            })

@socketio.on('leave_edit')
def handle_leave_edit(data):
    file_id = data['file_id']
    file = SharedFile.query.get(file_id)
    
    if file and file.active_editors > 0:
        file.active_editors -= 1
        db.session.commit()
        leave_room(f'edit_{file_id}')
        send({
            'type': 'system',
            'message': f'{current_user.email} 离开了编辑'
        }, room=f'edit_{file_id}')

@socketio.on('text_change')
def handle_text_change(data):
    file_id = data['file_id']
    file = SharedFile.query.get(file_id)
    
    if file and (file.allow_edit or current_user.id == file.user_id):
        send({
            'type': 'text_change',
            'content': data['content'],
            'user': current_user.email
        }, room=f'edit_{file_id}')

@app.route('/save/<int:file_id>', methods=['POST'])
@login_required
def save_file(file_id):
    file = SharedFile.query.get_or_404(file_id)
    if not file.allow_edit and file.user_id != current_user.id:
        flash('没有权限保存该文件')
        return redirect(url_for('collaborative_edit'))
    
    content = request.form.get('content')
    if not content:
        flash('内容不能为空')
        return redirect(url_for('edit_file', file_id=file_id))
    
    try:
        with open(os.path.join(app.config['UPLOAD_FOLDER'], file.filename), 'w', encoding='utf-8') as f:
            f.write(content)
        flash('文件保存成功')
    except Exception as e:
        flash('文件保存失败')
    
    return redirect(url_for('collaborative_edit'))

@app.route('/delete/<int:file_id>', methods=['POST'])
@login_required
def delete_file(file_id):
    file = SharedFile.query.get_or_404(file_id)
    if file.user_id != current_user.id:
        flash('没有权限删除该文件')
        return redirect(url_for('collaborative_edit'))
    
    try:
        os.remove(os.path.join(app.config['UPLOAD_FOLDER'], file.filename))
        db.session.delete(file)
        db.session.commit()
        flash('文件删除成功')
    except Exception as e:
        db.session.rollback()
        flash('文件保存失败')
    
    return redirect(url_for('collaborative_edit'))

# 原有路由保持不变
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/messages')
@login_required
def get_messages():
    messages = ChatMessage.query.filter_by(room='global').order_by(ChatMessage.timestamp).all()
    return {
        'messages': [{
            'id': msg.id,
            'user': msg.user.email,
            'content': msg.content,
            'timestamp': msg.timestamp.strftime('%H:%M')
        } for msg in messages]
    }

@app.route('/send_message', methods=['POST'])
@login_required
def send_message():
    data = request.get_json()
    message = data.get('message')
    room = data.get('room', 'global')
    
    if not message:
        return {'status': 'error', 'message': '消息内容不能为空'}, 400
    
    # 保存消息到数据库
    new_message = ChatMessage(
        content=message,
        user_id=current_user.id,
        room=room
    )
    db.session.add(new_message)
    db.session.commit()
    
    # 记录日志
    log_chat_message(current_user, message, room)
    
    return {'status': 'success', 'message': '消息发送成功'}

@app.route('/chat')
@login_required
def chat():
    # 获取所有在线用户
    online_user_ids = list(online_users.keys())
    online_users_list = User.query.filter(User.id.in_(online_user_ids)).all() if online_user_ids else []
    return render_template('chat.html', 
        online_users=online_users_list,
        ChatMessage=ChatMessage
    )

@app.route('/private_chat/<user_ids>')
@login_required
def private_chat(user_ids):
    # 获取私聊用户
    user_ids = [int(id) for id in user_ids.split(',')]
    participants = User.query.filter(User.id.in_(user_ids)).all()
    room_id = f"private_{'_'.join(str(id) for id in sorted(user_ids))}"
    return render_template('private_chat.html', 
        participants=participants,
        ChatMessage=ChatMessage,
        room_id=room_id
    )

def log_chat_message(user, message, room='global'):
    # 记录聊天日志
    log_file = os.path.join('chat_logs', f'{room}.log')
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(log_file, 'a', encoding='utf-8') as f:
        f.write(f'{timestamp} | {user.email} | {message}\n')

@socketio.on('connect')
def handle_connect():
    if current_user.is_authenticated:
        online_users[current_user.id] = request.sid
        send({
            'type': 'system',
            'message': f'{current_user.email} 已上线'
        }, broadcast=True)

@socketio.on('disconnect')
def handle_disconnect():
    if current_user.is_authenticated:
        online_users.pop(current_user.id, None)
        send({
            'type': 'system',
            'message': f'{current_user.email} 已下线'
        }, broadcast=True)

@socketio.on('send_message')
def handle_send_message(data):
    if current_user.is_authenticated:
        message = data.get('message')
        room = data.get('room', 'global')
        
        # 保存消息到数据库
        new_message = ChatMessage(
            content=message,
            user_id=current_user.id,
            room=room
        )
        db.session.add(new_message)
        db.session.commit()
        
        # 记录日志
        log_chat_message(current_user, message, room)
        
        # 广播消息
        send({
            'type': 'new_message',
            'user': current_user.email,
            'message': message,
            'timestamp': datetime.now().strftime('%H:%M')
        }, room=room)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        user = User.query.filter_by(email=email).first()
        
        if not user:
            flash('邮箱或密码错误')
            return redirect(url_for('login'))
            
        if not user.is_approved:
            flash('您的账号正在等待管理员审批')
            return redirect(url_for('login'))
            
        if not check_password_hash(user.password_hash, password):
            flash('邮箱或密码错误')
            return redirect(url_for('login'))
            
        login_user(user)
        return redirect(url_for('index'))
        
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/admin/approve_users')
@login_required
def approve_users():
    if not current_user.is_admin:
        flash('没有权限访问该页面')
        return redirect(url_for('index'))
    
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template('approve_users.html', users=users)

@app.route('/admin/approve_user/<int:user_id>', methods=['POST'])
@login_required
def approve_user(user_id):
    if not current_user.is_admin:
        flash('没有权限执行该操作')
        return redirect(url_for('index'))
    
    user = User.query.get_or_404(user_id)
    user.is_approved = True
    db.session.commit()
    flash(f'已批准用户 {user.email}')
    return redirect(url_for('approve_users'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        
        if User.query.filter_by(email=email).first():
            flash('该邮箱已被注册')
            return redirect(url_for('register'))
            
        user = User(
            email=email,
            password_hash=generate_password_hash(password),
            is_approved=False
        )
        db.session.add(user)
        db.session.commit()
        
        flash('注册成功，请等待管理员审批')
        return redirect(url_for('login'))
    
    return render_template('register.html')

# 创建初始管理员账户
def create_admin_user():
    if not User.query.filter_by(email='admin@oa.com').first():
        admin = User(
            email='admin@oa.com',
            password_hash=generate_password_hash('admin123'),
            is_admin=True,
            is_approved=True
        )
        db.session.add(admin)
        db.session.commit()

# 确保在应用启动时创建管理员账户
with app.app_context():
    create_admin_user()

if __name__ == '__main__':
    app.debug = True
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['SQLALCHEMY_ECHO'] = True
    socketio.run(app, host='0.0.0.0', port=8080)
