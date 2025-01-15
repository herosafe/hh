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
# 维护在线用户列表
online_users = {}

socketio = SocketIO(app)

@socketio.on('connect')
def handle_connect():
    if current_user.is_authenticated:
        online_users[current_user.id] = {
            'email': current_user.email,
            'factory': current_user.factory,
            'department': current_user.department
        }
        socketio.emit('update_online_users', list(online_users.values()))

@socketio.on('disconnect')
def handle_disconnect():
    if current_user.is_authenticated and current_user.id in online_users:
        del online_users[current_user.id]
        socketio.emit('update_online_users', list(online_users.values()))

@socketio.on('get_online_users')
def handle_get_online_users():
    socketio.emit('online_users', list(online_users.values()))

# 消息模型
class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    room = db.Column(db.String(100), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

# 创建上传目录
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# 初始化Flask-Login
login_manager = LoginManager()
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(120), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    is_approved = db.Column(db.Boolean, default=False)
    factory = db.Column(db.String(50))
    department = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Announcement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))

class Schedule(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    start_time = db.Column(db.DateTime, nullable=False)
    end_time = db.Column(db.DateTime, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))

class SharedFile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    original_name = db.Column(db.String(255), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))

# 创建数据库表
with app.app_context():
    db.create_all()

# 初始化Flask-Login
login_manager.init_app(app)

@app.route('/')
def index():
    return render_template('index.html')

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

@app.route('/admin/approve_users')
@login_required
def approve_users():
    if not current_user.is_admin:
        return redirect(url_for('index'))
        
    pending_users = User.query.filter_by(is_approved=False).all()
    return render_template('approve_users.html', users=pending_users)

@app.route('/admin/approve_user/<int:user_id>')
@login_required
def approve_user(user_id):
    if not current_user.is_admin:
        return redirect(url_for('index'))
        
    user = User.query.get_or_404(user_id)
    user.is_approved = True
    db.session.commit()
    
    flash(f'已批准用户 {user.email}')
    return redirect(url_for('approve_users'))

@app.route('/chat')
@login_required
def chat():
    users = User.query.filter(User.id != current_user.id, User.is_approved == True).all()
    return render_template('chat.html', users=users)

@app.route('/chat/history/<room>')
@login_required
def chat_history(room):
    messages = Message.query.filter_by(room=room).order_by(Message.timestamp.asc()).all()
    return [{
        'id': msg.id,
        'content': msg.content,
        'sender_id': msg.sender_id,
        'sender': msg.sender,
        'timestamp': msg.timestamp.isoformat()
    } for msg in messages]

@socketio.on('join')
def on_join(data):
    room = data['room']
    join_room(room)
    send(f'{current_user.email} 加入了聊天室', room=room)

@socketio.on('leave')
def on_leave(data):
    room = data['room']
    leave_room(room)
    send(f'{current_user.email} 离开了聊天室', room=room)

@socketio.on('message')
def handle_message(data):
    room = data['room']
    message = data['message']
    
    # 保存消息到数据库
    new_message = Message(
        content=message,
        sender_id=current_user.id,
        room=room
    )
    db.session.add(new_message)
    db.session.commit()
    
    # 获取发送者信息
    sender = User.query.get(current_user.id)
    
    send({
        'sender': sender.email,
        'message': message,
        'timestamp': new_message.timestamp.strftime('%Y-%m-%d %H:%M:%S')
    }, room=room)

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
